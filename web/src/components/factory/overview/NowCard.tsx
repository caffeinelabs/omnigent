/**
 * The Overview's first card: what the factory is doing now, in plain words.
 * What waits on the viewer comes first, then the projects in progress, what
 * comes next (and what Atlas says), spend and health. Every line opens the
 * page it is about. The server says what (`…/status-summary`); this card
 * ages it against the cockpit's clock.
 */

import type { ReactNode } from "react";
import {
  ChevronRightIcon,
  CircleCheckIcon,
  CircleDotIcon,
  CircleHelpIcon,
  CirclePauseIcon,
  CircleXIcon,
  CoinsIcon,
  HourglassIcon,
  MessageSquareTextIcon,
  StarIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type {
  StatusHealthItem,
  StatusSummary,
  StatusWaitingItem,
  StatusWorkingItem,
} from "@/lib/factoryStatusApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { plural } from "../factoryFormat";
import { factoryPageHref, projectsPageHref } from "../factoryRoutes";
import { isMissingRoute } from "../agents/opsQueries";
import {
  OTHERS_SHOWN,
  WAITING_SHOWN,
  WORKING_SHOWN,
  attentionText,
  idleSpendText,
  nowHeadline,
  spendText,
  splitHealth,
  splitWaiting,
  waitedFor,
  waitedLong,
  waitingEmptyText,
  waitsOn,
  workingNote,
  workingStage,
} from "./nowCardModel";
import { useFactoryStatusSummary } from "./statusSummaryQuery";

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

function WaitingLine({
  item,
  nowS,
  showWho,
}: {
  item: StatusWaitingItem;
  nowS: number;
  showWho: boolean;
}) {
  const who = showWho ? ` · ${waitsOn(item)}` : "";
  const detail = item.detail ? ` — ${item.detail}` : "";
  return (
    <Line
      href={item.href}
      icon={waitingIcon(item)}
      meta={waitedFor(item.since, nowS)}
      metaLong={waitedLong(item.since, nowS)}
      testId={`now-waiting-${item.key}`}
      title={`${item.ref} · ${item.text}${who}${detail}`}
    >
      <span className="font-mono text-muted-foreground">{item.ref}</span>
      <span> · {item.text}</span>
      {who && <span className="text-muted-foreground">{who}</span>}
      {detail && <span className="text-muted-foreground">{detail}</span>}
    </Line>
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
  return (
    <Line
      href={item.href}
      icon={workingIcon(item)}
      meta={waitedFor(item.since, nowS)}
      testId={`now-working-${item.project}`}
      title={`${item.title} · ${workingStage(item)}${note ? ` — ${note}` : ""}`}
    >
      <span className="font-medium">{item.project}</span>
      <span className="text-muted-foreground"> · {workingStage(item)}</span>
      {note && (
        <span className={cn(item.problem ? "text-warning" : "text-muted-foreground")}>
          {" "}
          — {note}
        </span>
      )}
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

/** The card for a summary in hand (stories and tests render it directly). */
export function NowCardView({
  factoryId,
  summary,
  nowS,
}: {
  factoryId: string;
  summary: StatusSummary;
  nowS: number;
}) {
  const split = splitWaiting(summary);
  const needsYou = `${factoryPageHref(factoryId)}#needs-you`;
  const mineShown = split.mine.slice(0, WAITING_SHOWN);
  const working = summary.working.slice(0, WORKING_SHOWN);
  const idleSpend = idleSpendText(summary.spend);
  // Problems of projects listed under Working on fold into one line here.
  const health = splitHealth(summary.health.items, working);
  return (
    <section
      id="now"
      aria-label="Now"
      data-testid="factory-now"
      data-state={summary.state}
      className="@container flex min-w-0 scroll-mt-24 flex-col gap-3 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-ui font-semibold">Now</h2>
        <p data-testid="now-headline" className="min-w-0 text-sm">
          {nowHeadline(summary, nowS)}
        </p>
      </div>
      <div className="grid min-w-0 grid-cols-1 gap-x-8 gap-y-3 @2xl:grid-cols-2">
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
              {split.others.length > 0 && (
                <ul className="flex flex-col">
                  {split.others.slice(0, OTHERS_SHOWN).map((item) => (
                    <WaitingLine key={item.key} item={item} nowS={nowS} showWho />
                  ))}
                </ul>
              )}
            </>
          ) : (
            <ul className="flex flex-col">
              {mineShown.map((item) => (
                <WaitingLine key={item.key} item={item} nowS={nowS} showWho={false} />
              ))}
              {split.mine.length > mineShown.length && (
                <More href={needsYou} testId="now-waiting-more">
                  +{split.mine.length - mineShown.length} more waiting on you
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
              {summary.spend.idle ? "No active projects" : "Nothing in progress yet"}
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
          {summary.next.length === 0 && !summary.lead ? (
            <Quiet
              testId="now-next-empty"
              icon={<CircleDotIcon aria-hidden className={cn(ICON, "text-muted-foreground")} />}
            >
              Nothing queued
            </Quiet>
          ) : (
            <ul className="flex flex-col">
              {summary.next.map((item) => (
                <Line
                  key={item.project}
                  href={item.href}
                  icon={<HourglassIcon aria-hidden className={cn(ICON, "text-muted-foreground")} />}
                  meta={waitedFor(item.since, nowS)}
                  testId={`now-next-${item.project}`}
                  title={`${item.title}: ${item.reason}`}
                >
                  <span className="font-medium">{item.project}</span>
                  <span className="text-muted-foreground"> {item.reason}</span>
                </Line>
              ))}
              {summary.lead && (
                <Line
                  href={summary.lead.href}
                  icon={
                    <MessageSquareTextIcon
                      aria-hidden
                      className={cn(ICON, "text-muted-foreground")}
                    />
                  }
                  testId="now-lead"
                  title={`${summary.lead.who}: ${summary.lead.text}`}
                >
                  <span className="font-medium">{summary.lead.who}:</span>
                  <span className="text-muted-foreground"> “{summary.lead.text}”</span>
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
              title={spendText(summary.spend)}
            >
              {idleSpend && <span className="text-warning">{idleSpend} · </span>}
              <span className={cn(idleSpend && "text-muted-foreground")}>
                {spendText(summary.spend)}
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

/** The Now card over its read: a short placeholder while loading, nothing on an older server. */
export function NowCard({ factoryId, nowS }: { factoryId: string; nowS: number }) {
  const query = useFactoryStatusSummary(factoryId);
  if (query.data) return <NowCardView factoryId={factoryId} summary={query.data} nowS={nowS} />;
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
