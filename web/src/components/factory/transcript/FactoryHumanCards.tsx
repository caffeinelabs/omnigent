// Compact cards for what a human sent through the cockpit: controls (pause,
// preview, charter update …), answered decisions, PR reviews and the brief.
// Each names the human and shows their text.

import {
  ActivityIcon,
  ArrowUpRightIcon,
  BanIcon,
  CheckCheckIcon,
  ClipboardListIcon,
  FilePenIcon,
  GitPullRequestIcon,
  MonitorOffIcon,
  MonitorPlayIcon,
  PauseIcon,
  PlayIcon,
  RocketIcon,
  RotateCcwIcon,
  SlidersHorizontalIcon,
  SquareIcon,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import type { FactoryMarkerTone, ParsedFactoryMessage } from "@/lib/factoryMessages";
import { cn } from "@/lib/utils";
import { FACTORY_CARD_CLASS, FACTORY_CARD_HEADER_CLASS, KindPill } from "./FactoryMessageCard";
import {
  CARD_LINK_CLASS,
  FactoryLink,
  cockpitHref,
  useFactoryTranscriptEnv,
} from "./FactoryTranscriptContext";
import { FoldableMarkdown } from "./FoldableMarkdown";
import { TONE_ICON, clockTime, shortSha } from "./factoryTone";

interface ControlVisual {
  icon: LucideIcon;
  label: (pr?: number) => string;
  tone: FactoryMarkerTone;
}

const CONTROL_VISUALS: Record<string, ControlVisual> = {
  PAUSE: { icon: PauseIcon, label: () => "Pause", tone: "warning" },
  RESUME: { icon: PlayIcon, label: () => "Resume", tone: "success" },
  STOP: { icon: SquareIcon, label: () => "Stop", tone: "danger" },
  RESTART: { icon: RotateCcwIcon, label: () => "Restart", tone: "warning" },
  RELAUNCHED: { icon: RocketIcon, label: () => "Relaunched with spec", tone: "info" },
  PREVIEW: {
    icon: MonitorPlayIcon,
    label: (pr) => (pr !== undefined ? `Preview PR #${pr}` : "Preview"),
    tone: "info",
  },
  PREVIEW_DOWN: {
    icon: MonitorOffIcon,
    label: (pr) => (pr !== undefined ? `Tear down preview of PR #${pr}` : "Tear down preview"),
    tone: "muted",
  },
  "ENV STATUS": { icon: ActivityIcon, label: () => "Env status", tone: "info" },
  "CHARTER UPDATE": { icon: FilePenIcon, label: () => "Charter update", tone: "accent" },
};

function controlVisual(action: string | undefined): ControlVisual {
  return (
    CONTROL_VISUALS[action ?? ""] ?? {
      icon: SlidersHorizontalIcon,
      label: () => action ?? "Control",
      tone: "muted",
    }
  );
}

function CardShell({
  testId,
  header,
  children,
  data,
}: {
  testId: string;
  header: ReactNode;
  children?: ReactNode;
  data?: Record<string, string | undefined>;
}) {
  return (
    <div className={FACTORY_CARD_CLASS} data-testid={testId} {...data}>
      <div className={FACTORY_CARD_HEADER_CLASS}>{header}</div>
      {children}
    </div>
  );
}

function ByLine({ by, time }: { by?: string; time: string | null }) {
  return (
    <>
      {by && (
        <span className="min-w-0 truncate">
          by <span className="text-foreground">{by}</span>
        </span>
      )}
      {time && <span className="shrink-0 tabular-nums">· {time}</span>}
    </>
  );
}

function CockpitAction({
  href,
  label,
  testId,
}: {
  href: string | null;
  label: string;
  testId: string;
}) {
  if (!href) return null;
  return (
    <FactoryLink to={href} className={cn(CARD_LINK_CLASS, "ml-auto")} testId={testId}>
      {label}
      <ArrowUpRightIcon className="size-3.5" aria-hidden />
    </FactoryLink>
  );
}

/**
 * `[Factory control · PAUSE|RESUME|…|PREVIEW pr=#n · by <human>] <note>`:
 * the action with its icon, the human, and the note. A PREVIEW control links
 * to that PR's review in the cockpit.
 */
export function ControlCard({
  parsed,
  createdAtS,
}: {
  parsed: ParsedFactoryMessage;
  createdAtS?: number;
}) {
  const { factoryId } = useFactoryTranscriptEnv();
  const visual = controlVisual(parsed.action);
  const Icon = visual.icon;
  const isPreview = parsed.action === "PREVIEW" || parsed.action === "PREVIEW_DOWN";
  return (
    <CardShell
      testId="factory-control-card"
      data={{ "data-action": parsed.action }}
      header={
        <>
          <Icon className={cn("size-3.5 shrink-0", TONE_ICON[visual.tone])} aria-hidden />
          <strong className="shrink-0 font-semibold text-foreground">Control</strong>
          <KindPill label={visual.label(parsed.pr)} tone={visual.tone} />
          <ByLine by={parsed.by} time={clockTime(createdAtS)} />
          {isPreview && parsed.pr !== undefined && (
            <CockpitAction
              href={cockpitHref(factoryId, { review: parsed.pr })}
              label={`Review PR #${parsed.pr}`}
              testId="factory-control-open-review"
            />
          )}
        </>
      }
    >
      {parsed.body && <FoldableMarkdown text={parsed.body} />}
    </CardShell>
  );
}

/**
 * `[Factory decision · D-n answered by <human> · "<option>"]`: the option
 * the human picked, their note and the question it answers. A withdrawn
 * decision renders as a one-line card.
 */
export function DecisionAnsweredCard({
  parsed,
  createdAtS,
}: {
  parsed: ParsedFactoryMessage;
  createdAtS?: number;
}) {
  const { factoryId } = useFactoryTranscriptEnv();
  const Icon = parsed.withdrawn ? BanIcon : CheckCheckIcon;
  return (
    <CardShell
      testId="factory-decision-card"
      data={{
        "data-decision": parsed.decisionId,
        "data-withdrawn": parsed.withdrawn ? "true" : undefined,
      }}
      header={
        <>
          <Icon
            className={cn(
              "size-3.5 shrink-0",
              parsed.withdrawn ? "text-muted-foreground" : "text-success",
            )}
            aria-hidden
          />
          <strong className="shrink-0 font-semibold text-foreground">{parsed.decisionId}</strong>
          <span className="shrink-0">{parsed.withdrawn ? "withdrawn" : "answered"}</span>
          <ByLine by={parsed.by} time={clockTime(createdAtS)} />
          <CockpitAction
            href={cockpitHref(factoryId, { decision: parsed.decisionId })}
            label={`Open ${parsed.decisionId}`}
            testId="factory-decision-open"
          />
        </>
      }
    >
      {!parsed.withdrawn && parsed.option !== undefined && (
        <p className="min-w-0 break-words font-medium" data-testid="factory-decision-option">
          “{parsed.option}”
        </p>
      )}
      {parsed.body && <FoldableMarkdown text={parsed.body} />}
      {parsed.question && (
        <p className="min-w-0 break-words text-sm text-muted-foreground">
          <span className="font-medium">Question:</span> {parsed.question}
        </p>
      )}
    </CardShell>
  );
}

/**
 * `[Factory review · CHANGES REQUESTED|APPROVED … PR #n @sha by <human> (cockpit)]`:
 * the verdict, the PR at the reviewed commit, and the human's text.
 */
export function ReviewCard({
  parsed,
  createdAtS,
}: {
  parsed: ParsedFactoryMessage;
  createdAtS?: number;
}) {
  const { factoryId } = useFactoryTranscriptEnv();
  const approved = parsed.verdict === "approved";
  const sha = shortSha(parsed.sha);
  return (
    <CardShell
      testId="factory-review-card"
      data={{ "data-verdict": parsed.verdict }}
      header={
        <>
          <GitPullRequestIcon
            className={cn("size-3.5 shrink-0", approved ? "text-success" : "text-warning")}
            aria-hidden
          />
          <strong className="shrink-0 font-semibold text-foreground">Review</strong>
          <KindPill
            label={approved ? "Approved" : "Changes requested"}
            tone={approved ? "success" : "warning"}
          />
          {parsed.pr !== undefined && (
            <span className="shrink-0">
              PR #{parsed.pr}
              {sha && <span className="font-mono tabular-nums"> @{sha}</span>}
            </span>
          )}
          <ByLine
            by={parsed.by && parsed.source ? `${parsed.by} (${parsed.source})` : parsed.by}
            time={clockTime(createdAtS)}
          />
          <CockpitAction
            href={parsed.pr !== undefined ? cockpitHref(factoryId, { review: parsed.pr }) : null}
            label="Open review"
            testId="factory-review-open"
          />
        </>
      }
    >
      {parsed.body && (
        <div className="border-l-2 border-border pl-3 text-ui">
          <FoldableMarkdown text={parsed.body} />
        </div>
      )}
    </CardShell>
  );
}

/** `[Factory brief · from <human> via cockpit]`: the human's brief, folded past 8 lines. */
export function BriefCard({
  parsed,
  createdAtS,
}: {
  parsed: ParsedFactoryMessage;
  createdAtS?: number;
}) {
  return (
    <CardShell
      testId="factory-brief-card"
      data={{ "data-mail-id": parsed.mailId }}
      header={
        <>
          <ClipboardListIcon className="size-3.5 shrink-0 text-brand-accent" aria-hidden />
          <strong className="shrink-0 font-semibold text-foreground">Brief</strong>
          <span className="min-w-0 truncate">
            from <span className="text-foreground">{parsed.by}</span>
            {parsed.source === "cockpit" && " via cockpit"}
          </span>
          {clockTime(createdAtS) && (
            <span className="shrink-0 tabular-nums">· {clockTime(createdAtS)}</span>
          )}
        </>
      }
    >
      {parsed.body && <FoldableMarkdown text={parsed.body} />}
    </CardShell>
  );
}
