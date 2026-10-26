/**
 * The chat-header factory strip: where this session sits in its factory.
 *
 *   PL:  [factory] Atlas factory › api-version · ● Verify 18m · PR #9630 ●●●●●● · Preview ● · $7.10 · Open cockpit →
 *   IC:  [factory] Atlas factory › api-version › ic_codex:version-endpoint · PR #9630 ●●●●●● · Open cockpit →
 *
 * Sub-agents carry no factory labels, so their root session's labels are
 * read. The head (name › project · stage) never wraps and truncates the name,
 * then the project; the other segments wrap onto a clipped second line when
 * the header is narrow, so the least important drop out whole.
 */

import type { ReactNode } from "react";
import { ArrowRightIcon, ChevronRightIcon, FactoryIcon } from "lucide-react";
import { useFactory } from "@/hooks/useFactory";
import { useNow } from "@/hooks/useNow";
import { useRootSessionId, useSession } from "@/hooks/useSession";
import { isFeatureEnabled } from "@/lib/capabilities";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import {
  FACTORY_ID_LABEL_KEY,
  FACTORY_NAME_LABEL_KEY,
  FACTORY_PROJECT_LABEL_KEY,
  FACTORY_ROLE_LABEL_KEY,
} from "@/lib/factory";
import {
  READY_GATES,
  type FactorySnapshot,
  type GatesView,
  type PrView,
  type ProjectView,
} from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { MOBILE_GLASS_PILL } from "@/shell/mobileGlass";
import {
  type FactoryTone,
  GATE_LABEL,
  STAGE_LABEL,
  TONE_DOT,
  TONE_TEXT,
  formatElapsed,
  formatUsd,
  gateTone,
  plural,
  previewStatus,
  primaryPr,
  projectPrs,
  stageText,
  stageTone,
} from "./factoryFormat";

/** Where a session sits in a factory, from its own labels or its root's. */
export interface FactoryPlacement {
  factoryId: string;
  /** `null` for a lead. */
  project: string | null;
  role: string | null;
  /** The session is a sub-agent of the member named by these labels. */
  viaRoot: boolean;
  /** The session's own title (an IC's task name). */
  title: string | null;
  /** The factory name label (set on the root only). */
  nameLabel: string | null;
}

function placementFrom(
  labels: Record<string, string> | undefined,
  viaRoot: boolean,
  title: string | null,
): FactoryPlacement | null {
  const factoryId = labels?.[FACTORY_ID_LABEL_KEY]?.trim();
  if (!factoryId) return null;
  return {
    factoryId,
    project: labels?.[FACTORY_PROJECT_LABEL_KEY]?.trim() || null,
    role: labels?.[FACTORY_ROLE_LABEL_KEY]?.trim() || null,
    viaRoot,
    title,
    nameLabel: labels?.[FACTORY_NAME_LABEL_KEY]?.trim() || null,
  };
}

/** Resolve a session's factory placement; `null` for non-factory sessions. */
export function useFactoryPlacement(sessionId: string | null | undefined): FactoryPlacement | null {
  const { session } = useSession(sessionId);
  const own = placementFrom(session?.labels, false, session?.title ?? null);
  const parentId = own ? null : session?.parentSessionId;
  const rootId = useRootSessionId(parentId ? (sessionId ?? null) : null, parentId);
  const { session: root } = useSession(rootId && rootId !== sessionId ? rootId : null);
  return own ?? (root ? placementFrom(root.labels, true, session?.title ?? null) : null);
}

/** One dot per ready gate (plus the demo badge when the project records demos). */
export function GateDots({ gates, className }: { gates: GatesView; className?: string }) {
  const entries: [string, FactoryTone, string][] = READY_GATES.map((gate) => [
    gate,
    gateTone(gates[gate]),
    `${GATE_LABEL[gate]} ${gates[gate]}`,
  ]);
  if (gates.demo != null) entries.push(["demo", gateTone(gates.demo), `Demo ${gates.demo}`]);
  const summary = entries.map(([, , text]) => text).join(" · ");
  return (
    <span
      role="img"
      aria-label={`Gates: ${summary}`}
      title={summary}
      data-testid="factory-gate-dots"
      className={cn("inline-flex items-center gap-0.5", className)}
    >
      {entries.map(([key, tone]) => (
        <span
          key={key}
          data-gate={key}
          data-tone={tone}
          className={cn("size-1.5 rounded-full", TONE_DOT[tone])}
        />
      ))}
    </span>
  );
}

function Segment({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex h-7 shrink-0 items-center gap-1.5 pr-2.5", className)}>
      {children}
    </span>
  );
}

function PrSegment({ pr }: { pr: PrView }) {
  return (
    <Segment>
      <span className={cn("font-mono tabular-nums", pr.state === "merged" && "text-brand-accent")}>
        PR #{pr.number}
      </span>
      {pr.state === "open" ? (
        <GateDots gates={pr.gates} />
      ) : (
        <span className={pr.state === "merged" ? "text-brand-accent" : undefined}>{pr.state}</span>
      )}
    </Segment>
  );
}

/** The derived stage (kept in the strip's head, so a narrow header never drops it). */
function StageLabel({ project }: { project: ProjectView }) {
  const tone = stageTone(project.stage, project.overlay);
  return (
    <span
      data-testid="factory-strip-stage"
      data-stage={project.stage}
      className={cn("inline-flex shrink-0 items-center gap-1.5 pr-1.5", TONE_TEXT[tone])}
    >
      <span aria-hidden className={cn("size-1.5 rounded-full", TONE_DOT[tone])} />
      {stageText(project.stage, project.overlay)}
    </span>
  );
}

/** PR, preview and cost of a project (only the PR for a sub-agent). */
function ProjectSegments({
  snapshot,
  project,
  placement,
  nowS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  placement: FactoryPlacement;
  nowS: number;
}) {
  const prs = projectPrs(snapshot, project);
  const pr = primaryPr(prs, placement.viaRoot ? placement.title : null);
  if (placement.viaRoot) return pr ? <PrSegment pr={pr} /> : null;
  const inStage = formatElapsed(nowS - project.stage_since);
  const preview = pr?.state === "open" ? previewStatus(pr.preview, pr.head_sha) : null;
  const cost = formatUsd(project.cost_usd);
  return (
    <>
      {inStage && (
        <Segment>
          <span className="tabular-nums" title="Time in this stage">
            {inStage}
          </span>
        </Segment>
      )}
      {pr && <PrSegment pr={pr} />}
      {pr?.preview && preview && (
        <Segment>
          Preview
          <span
            role="img"
            aria-label={`Preview ${preview.label}`}
            title={`Preview ${preview.label}`}
            data-testid="factory-strip-preview"
            data-tone={preview.tone}
            className={cn("size-1.5 rounded-full", TONE_DOT[preview.tone])}
          />
        </Segment>
      )}
      {cost && (
        <Segment>
          <span className="font-mono tabular-nums">{cost}</span>
        </Segment>
      )}
    </>
  );
}

function LeadSegments({ snapshot }: { snapshot: FactorySnapshot }) {
  const { totals } = snapshot;
  const cost = formatUsd(snapshot.factory.cost_usd);
  return (
    <>
      <Segment>{plural(snapshot.projects.length, "project")}</Segment>
      {totals.prs_ready > 0 && (
        <Segment className="text-success">{totals.prs_ready} ready for review</Segment>
      )}
      {totals.decisions_open > 0 && (
        <Segment className="text-warning">{plural(totals.decisions_open, "decision")}</Segment>
      )}
      {cost && (
        <Segment>
          <span className="font-mono tabular-nums">{cost}</span>
        </Segment>
      )}
    </>
  );
}

/** The phone header's stand-in: a glass icon button with the headline tone. */
function MobileStripButton({
  href,
  label,
  tone,
}: {
  href: string;
  label: string;
  tone: FactoryTone | null;
}) {
  return (
    <Link
      to={href}
      componentId="chat.header.factory_strip"
      data-testid="factory-strip-mobile"
      aria-label={label}
      className={cn(
        "relative inline-flex size-10 shrink-0 items-center justify-center text-muted-foreground md:hidden",
        MOBILE_GLASS_PILL,
      )}
    >
      <FactoryIcon aria-hidden className="size-5" />
      {tone && (
        <span
          aria-hidden
          className={cn(
            "absolute top-2 right-2 size-2 rounded-full ring-2 ring-background",
            TONE_DOT[tone],
          )}
        />
      )}
    </Link>
  );
}

function headlineTone(
  snapshot: FactorySnapshot,
  project: ProjectView | undefined,
): FactoryTone | null {
  if (project) return stageTone(project.stage, project.overlay);
  if (snapshot.totals.decisions_open > 0) return "warning";
  return snapshot.totals.prs_ready > 0 ? "pass" : null;
}

function StripBody({ placement }: { placement: FactoryPlacement }) {
  const { data: snapshot } = useFactory(placement.factoryId);
  const now = useNow();
  const project = placement.project
    ? snapshot?.projects.find((p) => p.slug === placement.project)
    : undefined;
  const name = snapshot?.factory.name ?? placement.nameLabel ?? "Factory";
  const href = placement.project
    ? `/factory/${placement.factoryId}?project=${encodeURIComponent(placement.project)}`
    : `/factory/${placement.factoryId}`;
  const stageLabel = project ? STAGE_LABEL[project.stage] : null;
  const label = `Open cockpit: ${[name, placement.project, stageLabel].filter(Boolean).join(" · ")}`;
  const showStage = project !== undefined && !placement.viaRoot;
  return (
    <>
      <MobileStripButton
        href={href}
        label={label}
        tone={snapshot ? headlineTone(snapshot, project) : null}
      />
      <Link
        to={href}
        componentId="chat.header.factory_strip"
        data-testid="factory-strip"
        data-live={snapshot ? "true" : "false"}
        aria-label={label}
        className={cn(
          // basis-0 + grow up to its content: the breadcrumb is sized first and the
          // strip takes what is left, shedding whole segments when that is short.
          "group hidden h-7 min-w-0 grow basis-0 max-w-max flex-wrap items-center overflow-hidden rounded-md border border-border bg-card/60 pl-2 text-sm text-muted-foreground transition-colors md:flex",
          "hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
        )}
      >
        <span
          data-testid="factory-strip-place"
          className={cn(
            "inline-flex h-7 min-w-0 shrink items-center gap-1.5",
            !showStage && "pr-2.5",
          )}
        >
          <FactoryIcon aria-hidden className="size-3.5 shrink-0" />
          <span className="min-w-0 shrink-[100] truncate font-medium text-foreground">{name}</span>
          {/* The chevrons ride inside the truncating spans, so a squeezed name
              never leaves a lone separator behind. */}
          {placement.project && (
            <span className="min-w-0 truncate text-foreground">
              <ChevronRightIcon
                aria-hidden
                className="mr-1.5 inline size-3 align-[-1px] opacity-60"
              />
              {placement.project}
            </span>
          )}
          {placement.viaRoot && placement.title && (
            <span className="min-w-0 max-w-48 shrink-[10] truncate">
              <ChevronRightIcon
                aria-hidden
                className="mr-1.5 inline size-3 align-[-1px] opacity-60"
              />
              {placement.title}
            </span>
          )}
          {project && showStage && <StageLabel project={project} />}
        </span>
        {snapshot && project && (
          <ProjectSegments
            snapshot={snapshot}
            project={project}
            placement={placement}
            nowS={Math.floor(now.getTime() / 1000)}
          />
        )}
        {snapshot && !placement.project && <LeadSegments snapshot={snapshot} />}
        <Segment className="text-foreground">
          Open cockpit
          <ArrowRightIcon
            aria-hidden
            className="size-3.5 transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none"
          />
        </Segment>
      </Link>
    </>
  );
}

/**
 * The strip for `sessionId`; renders nothing for non-factory sessions or when
 * the factory feature is off. Desktop only (the phone header has no room).
 */
export function FactoryStrip({ sessionId }: { sessionId: string | null | undefined }) {
  const enabled = isFeatureEnabled(useServerInfo(), "factory");
  return enabled ? <EnabledFactoryStrip sessionId={sessionId} /> : null;
}

function EnabledFactoryStrip({ sessionId }: { sessionId: string | null | undefined }) {
  const placement = useFactoryPlacement(sessionId);
  return placement ? <StripBody placement={placement} /> : null;
}
