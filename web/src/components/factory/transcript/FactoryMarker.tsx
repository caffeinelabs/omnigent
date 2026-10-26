import {
  ActivityIcon,
  ArrowUpRightIcon,
  ChevronRightIcon,
  CircleCheckIcon,
  CircleXIcon,
  GitMergeIcon,
  GitPullRequestClosedIcon,
  HandIcon,
  HeartIcon,
  HourglassIcon,
  InfoIcon,
  MailXIcon,
  MonitorPlayIcon,
  ReplyIcon,
  SlidersHorizontalIcon,
  TriangleAlertIcon,
  type LucideIcon,
} from "lucide-react";
import { useId, useState } from "react";
import {
  type FactoryMarkerTopic,
  type ParsedFactoryMessage,
  summarizeFactoryMarker,
} from "@/lib/factoryMessages";
import { cn } from "@/lib/utils";
import {
  CARD_LINK_CLASS,
  FactoryLink,
  cockpitHref,
  sessionHref,
  useFactoryTranscriptEnv,
} from "./FactoryTranscriptContext";
import { TONE_ICON, clockTime, clockTimeIso, formatIsoStamps } from "./factoryTone";

const TOPIC_ICON: Record<FactoryMarkerTopic, LucideIcon> = {
  heartbeat: HeartIcon,
  reply: ReplyIcon,
  ready: CircleCheckIcon,
  merged: GitMergeIcon,
  closed: GitPullRequestClosedIcon,
  checks: CircleXIcon,
  preview: MonitorPlayIcon,
  control: SlidersHorizontalIcon,
  alert: TriangleAlertIcon,
  stuck: HourglassIcon,
  storm: ActivityIcon,
  delivery: MailXIcon,
  needs_human: HandIcon,
  generic: InfoIcon,
};

/** Lines paired with keys that stay unique when two lines read the same. */
function withUniqueKeys(lines: string[]): [string, string][] {
  const seen = new Map<string, number>();
  return lines.map((line) => {
    const n = (seen.get(line) ?? 0) + 1;
    seen.set(line, n);
    return [`${n}:${line}`, line];
  });
}

/**
 * Compact centred row for factory markers (notices, alerts, delivery
 * failures, NEEDS HUMAN, heartbeats that did work, unknown factory headers),
 * styled like `SystemMessageView`. Detail (a request's last output, a failure
 * reason) folds behind the label; actions open the session it names and the
 * cockpit (at the decision or PR when the text names one).
 */
export function FactoryMarker({
  parsed,
  createdAtS,
}: {
  parsed: ParsedFactoryMessage;
  createdAtS?: number;
}) {
  const { factoryId } = useFactoryTranscriptEnv();
  const [open, setOpen] = useState(false);
  const detailId = useId();
  const summary = summarizeFactoryMarker(parsed);
  const Icon = TOPIC_ICON[summary.topic];
  const hasDetail = summary.detail.trim().length > 0;
  const time =
    parsed.kind === "heartbeat" ? (clockTimeIso(parsed.at) ?? clockTime(createdAtS)) : null;
  const urgent = parsed.kind === "needs_human";
  // Link the cockpit only where it has something specific to show.
  const cockpit =
    summary.decisionId || summary.pr !== undefined || urgent
      ? cockpitHref(
          factoryId,
          summary.decisionId
            ? { decision: summary.decisionId }
            : summary.pr !== undefined
              ? { review: summary.pr }
              : {},
        )
      : null;

  // The label wraps rather than truncates: it is the text the human came to read.
  const labelRow = (
    <>
      <Icon className={cn("mt-[3px] size-3.5 shrink-0", TONE_ICON[summary.tone])} aria-hidden />
      <span className="min-w-0 [overflow-wrap:anywhere]" data-testid="factory-marker-label">
        <strong className={cn("font-semibold", urgent && "text-warning")}>{summary.title}</strong>
        {summary.label && <> {formatIsoStamps(summary.label)}</>}
        {time && <span className="whitespace-nowrap tabular-nums"> · {time}</span>}
      </span>
    </>
  );

  return (
    <div
      className="not-prose my-1 flex w-full min-w-0 flex-col items-center gap-1 text-sm text-muted-foreground"
      data-testid="factory-marker"
      data-factory-kind={parsed.kind}
      data-topic={summary.topic}
    >
      {/* Label row and delta bullets share a left edge; the block is centred. */}
      <div className="flex max-w-full min-w-0 flex-col items-start gap-0.5">
        <div className="flex max-w-full min-w-0 flex-wrap items-center justify-center gap-x-2 gap-y-0.5">
          {hasDetail ? (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              aria-controls={detailId}
              className="flex max-w-full min-w-0 items-start gap-1.5 rounded px-1.5 py-0.5 text-left transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
            >
              {labelRow}
              <ChevronRightIcon
                className={cn(
                  "mt-[3px] size-3.5 shrink-0 transition-transform",
                  open && "rotate-90",
                )}
                aria-hidden
              />
            </button>
          ) : (
            <div className="flex max-w-full min-w-0 items-start gap-1.5 px-1.5 py-0.5">
              {labelRow}
            </div>
          )}
          {summary.sessionId && (
            <FactoryLink
              to={sessionHref(summary.sessionId)}
              className={CARD_LINK_CLASS}
              title={summary.sessionTitle ? `Open ${summary.sessionTitle}` : "Open session"}
              testId="factory-marker-open-session"
            >
              Open session
              <ArrowUpRightIcon className="size-3.5" aria-hidden />
            </FactoryLink>
          )}
          {cockpit && (
            <FactoryLink
              to={cockpit}
              className={CARD_LINK_CLASS}
              testId="factory-marker-open-cockpit"
            >
              {summary.decisionId ? `Open ${summary.decisionId}` : "Open in cockpit"}
              <ArrowUpRightIcon className="size-3.5" aria-hidden />
            </FactoryLink>
          )}
        </div>
        {parsed.delta && parsed.delta.length > 0 && (
          // Indented to the label text: px-1.5 + the icon + gap-1.5.
          <ul
            className="flex max-w-[48rem] min-w-0 flex-col gap-0.5 pl-[1.625rem] text-left"
            data-testid="factory-heartbeat-delta"
          >
            {withUniqueKeys(parsed.delta).map(([key, line]) => (
              <li key={key} className="flex min-w-0 gap-1.5">
                <span aria-hidden>•</span>
                <span className="min-w-0 [overflow-wrap:anywhere]">{line}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      {hasDetail && open && (
        <div
          id={detailId}
          className="mt-0.5 max-h-64 w-full max-w-[48rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted px-3 py-2 text-left text-sm text-muted-foreground"
        >
          {summary.detail}
        </div>
      )}
    </div>
  );
}
