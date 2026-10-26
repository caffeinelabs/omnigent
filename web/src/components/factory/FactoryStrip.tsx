/**
 * The chat-header factory strip: where this session sits in its factory.
 *
 *   PL:   [factory] Atlas factory › api-version ● Ready 4h 46m PR #9630 ●●●●●● Preview ● $7.10 Open cockpit →
 *   IC:   [factory] Atlas factory › api-version › ic_codex:version-endpoint PR #9630 ●●●●●● Open cockpit →
 *   Lead: [factory] Atlas factory 2 decisions 1 ready for review 3 projects $41.20 Open cockpit →
 *
 * Sub-agents carry no factory labels, so their root session's labels are
 * read. A narrow header drops the lowest-priority parts whole (see stripFit):
 * the stage, the PR gates and the arrow into the cockpit stay longest.
 */

import { createContext, type ReactNode, useContext, useRef } from "react";
import { ArrowRightIcon, ChevronRightIcon, FactoryIcon } from "lucide-react";
import { useFactory } from "@/hooks/useFactory";
import { useRootSessionId, useSession } from "@/hooks/useSession";
import { isFeatureEnabled } from "@/lib/capabilities";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import {
  FACTORY_ID_LABEL_KEY,
  FACTORY_NAME_LABEL_KEY,
  FACTORY_PROJECT_LABEL_KEY,
  FACTORY_ROLE_LABEL_KEY,
  normalizeFactoryId,
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
import { useFactoryNow } from "./factoryClock";
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
import { type FitPlan, NO_FIT, useStripFit } from "./stripFit";

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
  const raw = labels?.[FACTORY_ID_LABEL_KEY]?.trim();
  if (!raw) return null;
  return {
    factoryId: normalizeFactoryId(raw),
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

/** The cockpit link for a placement (its project's card for PLs and ICs). */
export function cockpitHref(placement: FactoryPlacement): string {
  const base = `/factory/${encodeURIComponent(placement.factoryId)}`;
  return placement.project ? `${base}?project=${encodeURIComponent(placement.project)}` : base;
}

/** One dot per ready gate (plus the demo gate when the project records demos). */
export function GateDots({
  gates,
  prNumber,
  className,
}: {
  gates: GatesView;
  prNumber?: number;
  className?: string;
}) {
  const entries: [string, FactoryTone, string][] = READY_GATES.map((gate) => [
    gate,
    gateTone(gates[gate]),
    `${GATE_LABEL[gate]} ${gates[gate]}`,
  ]);
  if (gates.demo != null) entries.push(["demo", gateTone(gates.demo), `Demo ${gates.demo}`]);
  const summary = entries.map(([, , text]) => text).join(" · ");
  const label = prNumber != null ? `PR #${prNumber} gates: ${summary}` : `Gates: ${summary}`;
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
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

/**
 * What a narrow header keeps longest (higher stays). A PL's project is low:
 * its breadcrumb already names it.
 */
const KEEP = {
  stage: 10,
  decisions: 10,
  gates: 9,
  ready: 9,
  preview: 8,
  prNumber: 7,
  projects: 7,
  icProject: 6,
  leadName: 6,
  elapsed: 6,
  plProject: 5,
  cta: 4,
  name: 3,
  cost: 2,
  title: 2,
} as const;

/** Truncating text parts: the widest they get, and the least before they hide. */
const NAME_TEXT = { max: 160, min: 64 };
const TITLE_TEXT = { max: 192, min: 64 };

const FitContext = createContext<FitPlan>(NO_FIT);

/** One part of the strip; hidden whole (or truncated) when the slot is short. */
function Part({
  fit,
  keep,
  text,
  className,
  children,
  ...data
}: {
  fit: string;
  keep: number;
  text?: { max: number; min: number };
  className?: string;
  children: ReactNode;
  [data: `data-${string}`]: string | undefined;
}) {
  const plan = useContext(FitContext);
  const hidden = plan.hidden.has(fit);
  const clamp = plan.clamp?.key === fit ? plan.clamp.width : undefined;
  return (
    <span
      {...data}
      data-fit-key={fit}
      data-fit-priority={keep}
      data-fit-max={text?.max}
      data-fit-min={text?.min}
      data-fit-hidden={hidden ? "" : undefined}
      style={text ? { maxWidth: clamp ?? text.max } : undefined}
      className={cn(
        "inline-flex h-7 shrink-0 items-center gap-1.5 pr-2.5",
        text && "min-w-0",
        className,
        hidden && "hidden",
      )}
    >
      {children}
    </span>
  );
}

/** Text that truncates inside a {@link Part} with `text`. */
function FitText({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span data-fit-text className={cn("min-w-0 truncate", className)}>
      {children}
    </span>
  );
}

const CHEVRON = "size-3 shrink-0 opacity-60";

function PrParts({ pr }: { pr: PrView }) {
  const merged = pr.state === "merged";
  return (
    <>
      <Part fit="pr" keep={KEEP.prNumber} className="pr-1.5">
        <span className={cn("font-mono tabular-nums", merged && "text-brand-accent")}>
          PR #{pr.number}
        </span>
      </Part>
      <Part fit="gates" keep={KEEP.gates}>
        {pr.state === "open" ? (
          <GateDots gates={pr.gates} prNumber={pr.number} />
        ) : (
          <span className={merged ? "text-brand-accent" : undefined}>{pr.state}</span>
        )}
      </Part>
    </>
  );
}

/** Stage, time in stage, PR, preview and cost of a PL's project. */
function ProjectParts({
  snapshot,
  project,
  nowS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
}) {
  const pr = primaryPr(projectPrs(snapshot, project));
  const inStage = formatElapsed(nowS - project.stage_since);
  const preview = pr?.state === "open" ? previewStatus(pr.preview, pr.head_sha) : null;
  const cost = formatUsd(project.cost_usd);
  return (
    <>
      {inStage && (
        <Part fit="elapsed" keep={KEEP.elapsed}>
          <span className="tabular-nums" title="Time in this stage">
            {inStage}
          </span>
        </Part>
      )}
      {pr && <PrParts pr={pr} />}
      {pr?.preview && preview && (
        <Part fit="preview" keep={KEEP.preview}>
          Preview
          <span
            role="img"
            aria-label={`Preview ${preview.label}`}
            title={`Preview ${preview.label}`}
            data-testid="factory-strip-preview"
            data-tone={preview.tone}
            className={cn("size-1.5 rounded-full", TONE_DOT[preview.tone])}
          />
        </Part>
      )}
      {cost && (
        <Part fit="cost" keep={KEEP.cost}>
          <span className="font-mono tabular-nums">{cost}</span>
        </Part>
      )}
    </>
  );
}

/** What needs the human first, then the factory's size and cost. */
function LeadParts({ snapshot }: { snapshot: FactorySnapshot }) {
  const { totals } = snapshot;
  const cost = formatUsd(snapshot.factory.cost_usd);
  return (
    <>
      {totals.decisions_open > 0 && (
        <Part fit="decisions" keep={KEEP.decisions} className="text-warning">
          {plural(totals.decisions_open, "decision")}
        </Part>
      )}
      {totals.prs_ready > 0 && (
        <Part fit="ready" keep={KEEP.ready} className="text-success">
          {totals.prs_ready} ready for review
        </Part>
      )}
      <Part fit="projects" keep={KEEP.projects}>
        {plural(snapshot.projects.length, "project")}
      </Part>
      {cost && (
        <Part fit="cost" keep={KEEP.cost}>
          <span className="font-mono tabular-nums">{cost}</span>
        </Part>
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
        "relative inline-flex size-11 shrink-0 items-center justify-center text-muted-foreground md:hidden",
        MOBILE_GLASS_PILL,
      )}
    >
      <FactoryIcon aria-hidden className="size-5" />
      {tone && (
        <span
          aria-hidden
          className={cn(
            "absolute top-2.5 right-2.5 size-2 rounded-full ring-2 ring-background",
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
  const { data: snapshot, isLoading } = useFactory(placement.factoryId);
  const now = useFactoryNow();
  const slotRef = useRef<HTMLDivElement>(null);
  const stripRef = useRef<HTMLAnchorElement>(null);
  const plan = useStripFit(slotRef, stripRef);
  const project = placement.project
    ? snapshot?.projects.find((p) => p.slug === placement.project)
    : undefined;
  // While the first snapshot loads, a placeholder stands in for an unlabelled name.
  const name = snapshot?.factory.name ?? placement.nameLabel ?? (isLoading ? null : "Factory");
  const href = cockpitHref(placement);
  const stageLabel = project ? STAGE_LABEL[project.stage] : null;
  const label = `Open cockpit: ${[name ?? "Factory", placement.project, stageLabel].filter(Boolean).join(" · ")}`;
  const isLead = !placement.project;
  const icPr =
    snapshot && project && placement.viaRoot
      ? primaryPr(projectPrs(snapshot, project), placement.title)
      : null;
  const tone = project && !placement.viaRoot ? stageTone(project.stage, project.overlay) : null;
  return (
    <>
      <MobileStripButton
        href={href}
        label={label}
        tone={snapshot ? headlineTone(snapshot, project) : null}
      />
      {/* Takes the header's leftover width; the strip inside hugs what fits. */}
      <div
        ref={slotRef}
        data-factory-strip-slot
        className="mr-2 hidden h-7 min-w-16 grow basis-0 items-center md:flex"
      >
        <FitContext.Provider value={plan}>
          <Link
            ref={stripRef}
            to={href}
            componentId="chat.header.factory_strip"
            data-testid="factory-strip"
            data-live={snapshot ? "true" : "false"}
            aria-label={label}
            title={label}
            className={cn(
              "group inline-flex h-7 max-w-full flex-nowrap items-center overflow-hidden rounded-md border border-border bg-card/60 pl-2 text-sm whitespace-nowrap text-muted-foreground transition-colors",
              "hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
            )}
          >
            <span aria-hidden className="inline-flex h-7 shrink-0 items-center pr-1.5">
              <FactoryIcon className="size-3.5" />
            </span>
            <Part
              fit="name"
              keep={isLead ? KEEP.leadName : KEEP.name}
              text={NAME_TEXT}
              className={isLead ? undefined : "pr-1.5"}
            >
              {name ? (
                <FitText className="font-medium text-foreground">{name}</FitText>
              ) : (
                <span
                  aria-hidden
                  data-testid="factory-strip-name-loading"
                  className="h-3 w-20 animate-pulse rounded bg-muted"
                />
              )}
              {/* The name owns the separator after it, so it never stands alone. */}
              {placement.project && <ChevronRightIcon aria-hidden className={CHEVRON} />}
            </Part>
            {placement.project && (
              <Part
                fit="project"
                keep={placement.viaRoot ? KEEP.icProject : KEEP.plProject}
                text={NAME_TEXT}
                className={tone ? "pr-2" : undefined}
              >
                <FitText className="text-foreground">{placement.project}</FitText>
              </Part>
            )}
            {placement.viaRoot && placement.title && (
              <Part fit="title" keep={KEEP.title} text={TITLE_TEXT}>
                <ChevronRightIcon aria-hidden className={CHEVRON} />
                <FitText>{placement.title}</FitText>
              </Part>
            )}
            {project && tone && (
              <>
                <span aria-hidden className="inline-flex h-7 shrink-0 items-center pr-1.5">
                  <span className={cn("size-1.5 rounded-full", TONE_DOT[tone])} />
                </span>
                <Part
                  fit="stage"
                  keep={KEEP.stage}
                  className={TONE_TEXT[tone]}
                  data-testid="factory-strip-stage"
                  data-stage={project.stage}
                >
                  {stageText(project.stage, project.overlay)}
                </Part>
              </>
            )}
            {snapshot && project && !placement.viaRoot && (
              <ProjectParts
                snapshot={snapshot}
                project={project}
                nowS={Math.floor(now.getTime() / 1000)}
              />
            )}
            {icPr && <PrParts pr={icPr} />}
            {snapshot && isLead && <LeadParts snapshot={snapshot} />}
            <Part fit="cta" keep={KEEP.cta} className="pr-1 text-foreground">
              Open cockpit
            </Part>
            <span
              aria-hidden
              className="inline-flex h-7 shrink-0 items-center pr-2 text-foreground"
            >
              <ArrowRightIcon className="size-3.5 transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none" />
            </span>
          </Link>
        </FitContext.Provider>
      </div>
    </>
  );
}

/**
 * The strip for `sessionId`; renders nothing for non-factory sessions or when
 * the factory feature is off. Phones get a single icon button instead.
 */
export function FactoryStrip({ sessionId }: { sessionId: string | null | undefined }) {
  const enabled = isFeatureEnabled(useServerInfo(), "factory");
  return enabled ? <EnabledFactoryStrip sessionId={sessionId} /> : null;
}

function EnabledFactoryStrip({ sessionId }: { sessionId: string | null | undefined }) {
  const placement = useFactoryPlacement(sessionId);
  return placement ? <StripBody placement={placement} /> : null;
}
