/**
 * The Overview's first card: what the factory is doing now, in plain words.
 * What waits on the viewer comes first, each with its next step (Review →,
 * the decision's answer, Open) and the Slack DMs it already got; then the
 * projects agents still work on (a project that only waits on a person is
 * listed once, under waiting), what comes next and what Atlas said, spend
 * and health. Every line opens the page it is about. The server says what
 * (`…/status-summary`, the builder the Slack digest uses too); this card
 * ages it against the cockpit's clock.
 */

import { type ReactNode, useState } from "react";
import {
  ChevronRightIcon,
  CircleCheckIcon,
  CircleDotIcon,
  CircleHelpIcon,
  CirclePauseIcon,
  CircleXIcon,
  ClockIcon,
  CoinsIcon,
  HourglassIcon,
  MessageSquareShareIcon,
  MessageSquareTextIcon,
  StarIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DecisionView, FactorySnapshot } from "@/lib/factoryApi";
import type {
  StatusHealthItem,
  StatusLeadNote,
  StatusNextItem,
  StatusSummary,
  StatusWaitingItem,
  StatusWorkingItem,
} from "@/lib/factoryStatusApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { DecisionCard } from "../decisions/DecisionCard";
import { plural } from "../factoryFormat";
import { factoryPageHref, projectsPageHref } from "../factoryRoutes";
import { isMissingRoute } from "../agents/opsQueries";
import {
  WORKING_SHOWN,
  attentionText,
  checkInText,
  idleSpendText,
  idleText,
  leadParts,
  leadWho,
  nowHeadline,
  receiptText,
  shownWaiting,
  spendText,
  splitHealth,
  splitWaiting,
  staleText,
  waitedFor,
  waitedLong,
  waitingDetail,
  waitingEmptyText,
  waitsOn,
  workingNote,
  workingNoteQuoted,
  workingStage,
} from "./nowCardModel";
import { useFactoryStatusSummary } from "./statusSummaryQuery";
import { DecisionAnswerButton, OpenButton, ReviewButton } from "./WaitingActions";

const ICON = "size-3.5";

function Section({
  title,
  testId,
  children,
  aside,
}: {
  title: string;
  testId: string;
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <div data-testid={testId} className="flex min-w-0 flex-col gap-1">
      <h3 className="flex items-baseline gap-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {title}
        {aside}
      </h3>
      {children}
    </div>
  );
}

/** One line that opens the page it is about. */
function Line({
  href,
  icon,
  meta,
  metaLong = false,
  testId,
  title,
  children,
}: {
  href: string;
  icon: ReactNode;
  meta?: string | null;
  /** The wait has gone on for a day: the age is called out. */
  metaLong?: boolean;
  testId: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <li className="min-w-0">
      <Link
        to={href}
        data-testid={testId}
        title={title}
        componentId="factory.overview.now_line"
        className="group -mx-1.5 flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <span className="flex w-4 shrink-0 justify-center">{icon}</span>
        <span className="min-w-0 flex-1 truncate max-sm:line-clamp-2 max-sm:whitespace-normal">
          {children}
        </span>
        {meta && (
          <span
            data-long={metaLong || undefined}
            className={cn(
              "shrink-0 font-mono text-xs tabular-nums",
              metaLong ? "font-medium text-warning" : "text-muted-foreground",
            )}
          >
            {meta}
          </span>
        )}
        <ChevronRightIcon
          aria-hidden
          className={cn(ICON, "shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100")}
        />
      </Link>
    </li>
  );
}

/** A plain sentence where a list would be: nothing waiting, idle, nothing queued. */
function Quiet({
  testId,
  icon,
  children,
}: {
  testId: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <p
      data-testid={testId}
      className="flex min-w-0 items-center gap-2 py-1 text-sm text-muted-foreground"
    >
      <span className="flex w-4 shrink-0 justify-center">{icon}</span>
      <span className="min-w-0">{children}</span>
    </p>
  );
}

function More({ href, testId, children }: { href: string; testId: string; children: ReactNode }) {
  return (
    <li className="min-w-0">
      <Link
        to={href}
        data-testid={testId}
        componentId="factory.overview.now_more"
        className="ml-6 rounded-sm text-sm text-muted-foreground hover:text-foreground hover:underline"
      >
        {children}
      </Link>
    </li>
  );
}

function waitingIcon(item: StatusWaitingItem): ReactNode {
  if (item.kind === "pr")
    return <StarIcon aria-hidden className={cn(ICON, "fill-current text-success")} />;
  if (item.kind === "incident") {
    return item.severity === "critical" ? (
      <CircleXIcon aria-hidden className={cn(ICON, "text-destructive")} />
    ) : (
      <TriangleAlertIcon aria-hidden className={cn(ICON, "text-warning")} />
    );
  }
  return item.urgent ? (
    <TriangleAlertIcon aria-hidden className={cn(ICON, "text-warning")} />
  ) : (
    <CircleHelpIcon aria-hidden className={cn(ICON, "text-muted-foreground")} />
  );
}

/** The next step beside a wait: Review →, the decision's answer (opens its card), or Open. */
function waitingAction(
  item: StatusWaitingItem,
  decision: DecisionView | null,
  open: boolean,
  toggle: () => void,
): ReactNode {
  const testId = `now-action-${item.key}`;
  if (item.kind === "pr") {
    return (
      <ReviewButton href={item.href} testId={testId} componentId="factory.overview.now_review" />
    );
  }
  if (item.kind === "decision" && decision) {
    return (
      <DecisionAnswerButton
        decision={decision}
        open={open}
        onToggle={toggle}
        testId={testId}
        componentId="factory.overview.now_decision"
      />
    );
  }
  return (
    <OpenButton
      href={item.href}
      testId={testId}
      componentId="factory.overview.now_open"
      label={item.kind === "decision" ? "Answer →" : "Open"}
    />
  );
}

/**
 * One wait: its line opens its page; the button beside it is the next step
 * (a decision's card opens in place); below, the viewer's Slack DMs about it.
 */
function WaitingRow({
  item,
  nowS,
  showWho,
  decision,
  clockOffsetS,
}: {
  item: StatusWaitingItem;
  nowS: number;
  showWho: boolean;
  decision: DecisionView | null;
  clockOffsetS: number;
}) {
  const [open, setOpen] = useState(false);
  const who = showWho ? ` · ${waitsOn(item)}` : "";
  const detailText = waitingDetail(item);
  const detail = detailText ? ` — ${detailText}` : "";
  const meta = waitedFor(item.since, nowS);
  const long = waitedLong(item.since, nowS);
  const receipts = receiptText(item.slack, nowS);
  return (
    <li data-testid={`now-waiting-row-${item.key}`} className="flex min-w-0 flex-col">
      <div className="flex min-w-0 items-center gap-2">
        <Link
          to={item.href}
          data-testid={`now-waiting-${item.key}`}
          title={`${item.ref} · ${item.text}${who}${detail}`}
          componentId="factory.overview.now_line"
          className="group -mx-1.5 flex min-w-0 flex-1 items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          <span className="flex w-4 shrink-0 justify-center">{waitingIcon(item)}</span>
          <span className="min-w-0 flex-1 truncate max-sm:line-clamp-2 max-sm:whitespace-normal">
            <span className="font-mono text-muted-foreground">{item.ref}</span>
            <span> · {item.text}</span>
            {who && <span className="text-muted-foreground">{who}</span>}
            {detail && <span className="text-muted-foreground">{detail}</span>}
          </span>
          {meta && (
            <span
              data-long={long || undefined}
              className={cn(
                "shrink-0 font-mono text-xs tabular-nums",
                long ? "font-medium text-warning" : "text-muted-foreground",
              )}
            >
              {meta}
            </span>
          )}
        </Link>
        <span className="shrink-0">
          {waitingAction(item, decision, open, () => setOpen((v) => !v))}
        </span>
      </div>
      {receipts && (
        <p
          data-testid={`now-slack-${item.key}`}
          title="Slack DMs to you about it"
          className="flex min-w-0 items-center gap-1 pb-0.5 pl-6 text-xs text-muted-foreground"
        >
          <MessageSquareShareIcon aria-hidden className="size-3 shrink-0" />
          <span className="truncate">{receipts}</span>
        </p>
      )}
      {open && decision && (
        <div className="pt-1 pb-2 pl-6">
          <DecisionCard decision={decision} clockOffsetS={clockOffsetS} />
        </div>
      )}
    </li>
  );
}

function workingIcon(item: StatusWorkingItem): ReactNode {
  if (item.problem) return <TriangleAlertIcon aria-hidden className={cn(ICON, "text-warning")} />;
  return item.running ? (
    <span aria-hidden className="size-2 animate-pulse rounded-full bg-session-active" />
  ) : (
    <CircleDotIcon aria-hidden className={cn(ICON, "text-muted-foreground")} />
  );
}

function WorkingLine({ item, nowS }: { item: StatusWorkingItem; nowS: number }) {
  const note = workingNote(item);
  const quoted = note && workingNoteQuoted(item) ? `its lead says “${note}”` : note;
  return (
    <Line
      href={item.href}
      icon={workingIcon(item)}
      meta={waitedFor(item.since, nowS)}
      testId={`now-working-${item.project}`}
      title={`${item.title} · ${workingStage(item)}${quoted ? ` — ${quoted}` : ""}`}
    >
      <span className="font-medium">{item.project}</span>
      <span className="text-muted-foreground"> · {workingStage(item)}</span>
      {quoted && (
        <span className={cn(item.problem ? "text-warning" : "text-muted-foreground")}>
          {" "}
          — {quoted}
        </span>
      )}
    </Line>
  );
}

/** `Atlas said 52m ago: “… #9826 …”`: a quote, the ids in it linked to their pages. */
function LeadLine({ lead, nowS }: { lead: StatusLeadNote; nowS: number }) {
  const who = leadWho(lead, nowS);
  return (
    <li
      data-testid="now-lead"
      title={`${who}: “${lead.text}”`}
      className="flex min-w-0 items-start gap-2 py-1 text-sm"
    >
      <span className="flex h-5 w-4 shrink-0 items-center justify-center">
        <MessageSquareTextIcon aria-hidden className={cn(ICON, "text-muted-foreground")} />
      </span>
      <p className="line-clamp-2 min-w-0 flex-1 break-words">
        <Link
          to={lead.href}
          data-testid="now-lead-who"
          componentId="factory.overview.now_lead"
          className="rounded-sm font-medium hover:underline focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          {who}
        </Link>
        <span>: </span>
        <span data-testid="now-lead-quote" className="text-muted-foreground">
          “
          {leadParts(lead).map((part) =>
            part.href ? (
              <Link
                key={part.at}
                to={part.href}
                componentId="factory.overview.now_lead_ref"
                className="rounded-sm font-mono text-foreground underline decoration-muted-foreground/50 underline-offset-2 hover:decoration-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                {part.text}
              </Link>
            ) : (
              <span key={part.at}>{part.text}</span>
            ),
          )}
          ”
        </span>
      </p>
    </li>
  );
}

function NextLine({ item, nowS }: { item: StatusNextItem; nowS: number }) {
  return (
    <Line
      href={item.href}
      icon={
        item.problem ? (
          <TriangleAlertIcon aria-hidden className={cn(ICON, "text-warning")} />
        ) : (
          <HourglassIcon aria-hidden className={cn(ICON, "text-muted-foreground")} />
        )
      }
      meta={waitedFor(item.since, nowS)}
      testId={`now-next-${item.project}`}
      title={`${item.title}: ${item.reason}`}
    >
      <span className="font-medium">{item.project}</span>
      <span className={item.problem ? "text-warning" : "text-muted-foreground"}>
        {" "}
        {item.reason}
      </span>
    </Line>
  );
}

function healthIcon(item: StatusHealthItem): ReactNode {
  switch (item.kind) {
    case "paused":
    case "project_paused":
      return <CirclePauseIcon aria-hidden className={cn(ICON, "text-warning")} />;
    case "failed":
      return <CircleXIcon aria-hidden className={cn(ICON, "text-destructive")} />;
    case "done":
      return <CircleCheckIcon aria-hidden className={cn(ICON, "text-success")} />;
    default:
      return <TriangleAlertIcon aria-hidden className={cn(ICON, "text-warning")} />;
  }
}

/** The factory itself paused or finished: said before spend. */
function isFactoryWide(item: StatusHealthItem): boolean {
  return item.kind === "paused" || item.kind === "done";
}

function HealthLine({ item }: { item: StatusHealthItem }) {
  return (
    <Line
      href={item.href}
      icon={healthIcon(item)}
      testId={`now-health-${item.kind}${item.project ? `-${item.project}` : ""}`}
      title={item.note ? `${item.text} — “${item.note}”` : item.text}
    >
      {item.text}
      {item.note && <span className="text-muted-foreground"> — “{item.note}”</span>}
    </Line>
  );
}

/** A refresh failed: the card keeps its last good read and says how old it is. */
export interface NowStale {
  /** When the shown read arrived (epoch seconds). */
  updatedAtS: number;
  onRetry: () => void;
}

/** The card for a summary in hand (stories and tests render it directly). */
export function NowCardView({
  factoryId,
  summary,
  nowS,
  stale = null,
  snapshot = null,
  clockOffsetS = 0,
}: {
  factoryId: string;
  summary: StatusSummary;
  nowS: number;
  stale?: NowStale | null;
  /** The live snapshot: decisions answer in place, and spend shows the header's $/h. */
  snapshot?: FactorySnapshot | null;
  clockOffsetS?: number;
}) {
  const split = splitWaiting(summary);
  const needsYou = `${factoryPageHref(factoryId)}#needs-you`;
  const shown = shownWaiting(split);
  const working = summary.working.slice(0, WORKING_SHOWN);
  const usdPerH = snapshot ? snapshot.totals.usd_per_h : undefined;
  const idleSpend = idleSpendText(summary.spend, usdPerH);
  // Problems of projects listed under Working on or Next fold into one line here;
  // a project that only waits on a listed wait is said there.
  const health = splitHealth(summary.health.items, [...working, ...summary.next], shown);
  const decisions = new Map((snapshot?.decisions ?? []).map((d) => [`decision:${d.id}`, d]));
  const row = (item: StatusWaitingItem, showWho: boolean) => (
    <WaitingRow
      key={item.key}
      item={item}
      nowS={nowS}
      showWho={showWho}
      decision={decisions.get(item.key) ?? null}
      clockOffsetS={clockOffsetS}
    />
  );
  const quietNext = summary.next.length === 0 && !summary.lead && !summary.check_in;
  return (
    <section
      id="now"
      aria-label="Now"
      data-testid="factory-now"
      data-state={summary.state}
      data-stale={stale ? "true" : undefined}
      className="@container flex min-w-0 scroll-mt-24 flex-col gap-3 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-ui font-semibold">Now</h2>
        <p data-testid="now-headline" className={cn("min-w-0 text-sm", stale && "opacity-60")}>
          {nowHeadline(summary)}
        </p>
      </div>
      {stale && (
        <div
          data-testid="now-stale"
          className="-mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm text-warning"
        >
          <TriangleAlertIcon aria-hidden className={cn(ICON, "shrink-0")} />
          <span className="min-w-0">{staleText(stale.updatedAtS, nowS)}</span>
          <Button
            size="xs"
            variant="outline"
            onClick={stale.onRetry}
            componentId="factory.overview.now_stale_retry"
          >
            Retry
          </Button>
        </div>
      )}
      <div
        className={cn(
          "grid min-w-0 grid-cols-1 gap-x-8 gap-y-3 @2xl:grid-cols-2",
          stale && "opacity-60",
        )}
      >
        <Section
          title="Waiting on you"
          testId="now-waiting"
          aside={
            split.mine.length > 0 ? (
              <span className="text-warning tabular-nums">{split.mine.length}</span>
            ) : null
          }
        >
          {split.mine.length === 0 ? (
            <>
              <Quiet
                testId="now-waiting-empty"
                icon={<CircleCheckIcon aria-hidden className={cn(ICON, "text-success")} />}
              >
                {waitingEmptyText(split)}
              </Quiet>
              {shown.length > 0 && (
                <ul className="flex flex-col">{shown.map((item) => row(item, true))}</ul>
              )}
            </>
          ) : (
            <ul className="flex flex-col">
              {shown.map((item) => row(item, false))}
              {split.mine.length > shown.length && (
                <More href={needsYou} testId="now-waiting-more">
                  +{split.mine.length - shown.length} more waiting on you
                </More>
              )}
              {split.others.length > 0 && (
                <More href={needsYou} testId="now-waiting-others">
                  {plural(split.others.length, "thing")} waiting on others
                </More>
              )}
            </ul>
          )}
        </Section>

        <Section title="Working on" testId="now-working">
          {working.length === 0 ? (
            <Quiet
              testId="now-working-empty"
              icon={<HourglassIcon aria-hidden className={cn(ICON, "text-muted-foreground")} />}
            >
              {summary.spend.idle ? idleText(summary.spend, nowS) : "Nothing in progress yet"}
            </Quiet>
          ) : (
            <ul className="flex flex-col">
              {working.map((item) => (
                <WorkingLine key={item.project} item={item} nowS={nowS} />
              ))}
              {summary.working.length > working.length && (
                <More href={projectsPageHref(factoryId)} testId="now-working-more">
                  +{plural(summary.working.length - working.length, "more project")}
                </More>
              )}
            </ul>
          )}
        </Section>

        <Section title="Next" testId="now-next">
          {quietNext ? (
            <Quiet
              testId="now-next-empty"
              icon={<CircleDotIcon aria-hidden className={cn(ICON, "text-muted-foreground")} />}
            >
              Nothing queued
            </Quiet>
          ) : (
            <ul className="flex flex-col">
              {summary.next.map((item) => (
                <NextLine key={item.project} item={item} nowS={nowS} />
              ))}
              {summary.lead && <LeadLine lead={summary.lead} nowS={nowS} />}
              {summary.check_in && (
                <Line
                  href={summary.check_in.href}
                  icon={<ClockIcon aria-hidden className={cn(ICON, "text-muted-foreground")} />}
                  testId="now-check-in"
                  title={checkInText(summary.check_in, nowS)}
                >
                  <span className="text-muted-foreground">
                    {checkInText(summary.check_in, nowS)}
                  </span>
                </Line>
              )}
            </ul>
          )}
        </Section>

        <Section title="Spend and health" testId="now-health">
          <ul className="flex flex-col">
            {health.listed.filter(isFactoryWide).map((item) => (
              <HealthLine key={item.kind} item={item} />
            ))}
            <Line
              href={summary.spend.href}
              icon={
                <CoinsIcon
                  aria-hidden
                  className={cn(ICON, idleSpend ? "text-warning" : "text-muted-foreground")}
                />
              }
              testId="now-spend"
              title={spendText(summary.spend, usdPerH)}
            >
              {idleSpend && <span className="text-warning">{idleSpend} · </span>}
              <span className={cn(idleSpend && "text-muted-foreground")}>
                {spendText(summary.spend, usdPerH)}
              </span>
            </Line>
            {health.attention.length > 0 && (
              <Line
                href={projectsPageHref(factoryId)}
                icon={
                  <TriangleAlertIcon
                    aria-hidden
                    className={cn(
                      ICON,
                      health.attention.some((i) => i.kind === "failed")
                        ? "text-destructive"
                        : "text-warning",
                    )}
                  />
                }
                testId="now-health-attention"
                title={attentionText(health.attention)}
              >
                {attentionText(health.attention)}
              </Line>
            )}
            {health.listed
              .filter((item) => !isFactoryWide(item))
              .map((item, i) => (
                <HealthLine key={`${item.kind}:${item.project ?? i}`} item={item} />
              ))}
          </ul>
          {summary.health.items.length === 0 && (
            <Quiet
              testId="now-health-ok"
              icon={<CircleCheckIcon aria-hidden className={cn(ICON, "text-success")} />}
            >
              Nothing is blocked or failing
            </Quiet>
          )}
        </Section>
      </div>
    </section>
  );
}

/**
 * The Now card over its read: a short placeholder while loading, nothing on an
 * older server, and the last good read (dimmed, with its time) when a refresh fails.
 */
export function NowCard({
  factoryId,
  nowS,
  snapshot = null,
  clockOffsetS = 0,
}: {
  factoryId: string;
  nowS: number;
  /** The live snapshot (the Overview's): decisions answer in place; spend shows the header's $/h. */
  snapshot?: FactorySnapshot | null;
  clockOffsetS?: number;
}) {
  const query = useFactoryStatusSummary(factoryId);
  if (query.data) {
    const stale =
      query.isError && query.dataUpdatedAt > 0
        ? {
            updatedAtS: Math.floor(query.dataUpdatedAt / 1000),
            onRetry: () => void query.refetch(),
          }
        : null;
    return (
      <NowCardView
        factoryId={factoryId}
        summary={query.data}
        nowS={nowS}
        stale={stale}
        snapshot={snapshot}
        clockOffsetS={clockOffsetS}
      />
    );
  }
  if (query.error) {
    // A server without the route: the panels below say it all.
    if (isMissingRoute(query.error)) return null;
    return (
      <section
        aria-label="Now"
        data-testid="factory-now-error"
        className="flex min-w-0 items-center gap-2 rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground max-md:p-3"
      >
        <span className="min-w-0 flex-1">Couldn’t load what the factory is doing right now.</span>
        <Button
          size="xs"
          variant="outline"
          onClick={() => void query.refetch()}
          componentId="factory.overview.now_retry"
        >
          Retry
        </Button>
      </section>
    );
  }
  return (
    <section
      aria-label="Now"
      aria-busy="true"
      data-testid="factory-now-loading"
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <h2 className="text-ui font-semibold">Now</h2>
      <p className="text-sm text-muted-foreground">Reading what the factory is doing…</p>
    </section>
  );
}
