// Compact rows for the factory runner tools in an assistant turn:
// "Roster · 6 members · 1 ⚠", "Asked D-3 ↗", "PR #9652 registered · ready ✖
// checks pending", "Preview #9652 ready ↗", and a demo publication card.

import {
  ArrowUpRightIcon,
  ChevronRightIcon,
  CircleHelpIcon,
  ClapperboardIcon,
  FlagIcon,
  GitPullRequestIcon,
  Loader2Icon,
  MonitorPlayIcon,
  ServerIcon,
  UserRoundPlusIcon,
  UsersIcon,
  type LucideIcon,
} from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { ToolState } from "@/lib/renderItems";
import { cn } from "@/lib/utils";
import { KindPill } from "./FactoryMessageCard";
import {
  CARD_LINK_CLASS,
  FactoryLink,
  cockpitHref,
  linkTargetKind,
  sessionHref,
  useFactoryTranscriptEnv,
} from "./FactoryTranscriptContext";
import {
  type FactoryToolSummary,
  type RosterRow,
  summarizeFactoryTool,
} from "./factoryToolSummary";
import { TONE_ICON } from "./factoryTone";

const TOOL_ICON: Record<string, LucideIcon> = {
  sys_factory_roster: UsersIcon,
  sys_factory_set: FlagIcon,
  sys_factory_spawn: UserRoundPlusIcon,
  sys_factory_ask: CircleHelpIcon,
  sys_factory_pr: GitPullRequestIcon,
  sys_factory_env: MonitorPlayIcon,
  sys_factory_demo: ClapperboardIcon,
};

/**
 * Turn an absolute link on this origin (the server stamps demo links with
 * its public domain) into an in-app path; leave other `http(s)` links absolute.
 *
 * @returns The link, or `null` for a target that is neither (`javascript:`, `//host` …).
 */
export function inAppPath(url: string): string | null {
  const kind = linkTargetKind(url);
  if (kind !== "external" || typeof window === "undefined") return kind ? url : null;
  try {
    const parsed = new URL(url);
    return parsed.origin === window.location.origin
      ? `${parsed.pathname}${parsed.search}${parsed.hash}`
      : url;
  } catch {
    return null;
  }
}

function RowLink({ to, label, testId }: { to: string; label: string; testId: string }) {
  return (
    <FactoryLink to={to} className={CARD_LINK_CLASS} testId={testId}>
      {label}
      <ArrowUpRightIcon className="size-3.5" aria-hidden />
    </FactoryLink>
  );
}

function beaconText(row: RosterRow): string {
  return (
    [row.health && row.health !== "ok" ? row.health : null, row.summary]
      .filter(Boolean)
      .join(" · ") || "—"
  );
}

// Below 28rem the Status and Beacon columns stack under the member's name.
const WIDE_ONLY = "@max-[28rem]/roster:hidden";

function RosterTable({ rows }: { rows: RosterRow[] }) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">No members.</p>;
  }
  return (
    <div className="@container/roster max-w-full overflow-x-auto">
      <table
        className="w-full min-w-[28rem] border-collapse text-left text-sm @max-[28rem]/roster:min-w-0"
        data-testid="factory-roster-table"
      >
        <thead className="text-muted-foreground">
          <tr className="border-b border-border">
            <th className="py-1 pr-3 font-medium">Member</th>
            <th className="py-1 pr-3 font-medium">Role</th>
            <th className={cn("py-1 pr-3 font-medium", WIDE_ONLY)}>Status</th>
            <th className={cn("py-1 font-medium", WIDE_ONLY)}>Beacon</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={`${row.depth}:${row.id || row.title}`}
              className="border-b border-border/50 last:border-0"
            >
              <td
                className={cn(
                  "max-w-[14rem] truncate py-1 pr-3 @max-[28rem]/roster:max-w-none @max-[28rem]/roster:whitespace-normal @max-[28rem]/roster:[overflow-wrap:anywhere]",
                  row.depth === 1 && "pl-4",
                )}
              >
                {row.id ? (
                  <FactoryLink to={sessionHref(row.id)} className="hover:underline">
                    {row.title}
                  </FactoryLink>
                ) : (
                  row.title
                )}
                <div
                  className={cn(
                    "hidden text-xs @max-[28rem]/roster:block",
                    row.warn ? "text-warning" : "text-muted-foreground",
                  )}
                >
                  {[row.status, beaconText(row)].filter((t) => t && t !== "—").join(" · ")}
                </div>
              </td>
              <td className="py-1 pr-3 whitespace-nowrap text-muted-foreground">
                {[row.role, row.project].filter(Boolean).join(" · ") || "—"}
              </td>
              <td className={cn("py-1 pr-3 whitespace-nowrap text-muted-foreground", WIDE_ONLY)}>
                {row.status ?? "—"}
              </td>
              <td
                className={cn(
                  "max-w-[18rem] truncate py-1",
                  row.warn ? "text-warning" : "text-muted-foreground",
                  WIDE_ONLY,
                )}
                title={row.summary ?? undefined}
              >
                {beaconText(row)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Facts({ facts }: { facts: [string, string][] }) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-sm">
      {facts.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-muted-foreground">{key}</dt>
          <dd className="min-w-0 break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function rowLinks(summary: FactoryToolSummary, factoryId: string | null): ReactNode[] {
  const links: ReactNode[] = [];
  if (summary.sessionId) {
    links.push(
      <RowLink
        key="session"
        to={sessionHref(summary.sessionId)}
        label="Open"
        testId="factory-tool-open-session"
      />,
    );
  }
  const cockpit = summary.cockpit ? cockpitHref(factoryId, summary.cockpit) : null;
  if (summary.tool === "sys_factory_env" && summary.externalUrl) {
    links.push(
      <RowLink
        key="preview"
        to={summary.externalUrl}
        label="Open preview"
        testId="factory-tool-open-preview"
      />,
    );
  } else if (cockpit) {
    const label =
      summary.tool === "sys_factory_ask" ? `Open ${String(summary.cockpit?.decision)}` : "Review";
    links.push(
      <RowLink key="cockpit" to={cockpit} label={label} testId="factory-tool-open-cockpit" />,
    );
  } else if (summary.tool === "sys_factory_pr" && summary.externalUrl) {
    links.push(
      <RowLink
        key="github"
        to={summary.externalUrl}
        label="GitHub"
        testId="factory-tool-open-github"
      />,
    );
  }
  return links;
}

/** Published demo: status, PR at the recorded commit, what it shows, and the review link. */
export function DemoPublishedCard({ summary }: { summary: FactoryToolSummary }) {
  const { factoryId } = useFactoryTranscriptEnv();
  const href =
    (summary.demoUrl ? inAppPath(summary.demoUrl) : null) ??
    (summary.cockpit ? cockpitHref(factoryId, summary.cockpit) : null);
  const files = summary.demoFiles ?? [];
  const counts = new Map<string, number>();
  for (const f of files) counts.set(f.kind, (counts.get(f.kind) ?? 0) + 1);
  const fileText = [...counts.entries()]
    .map(([kind, n]) => (n === 1 ? kind : `${n} ${kind}${kind.endsWith("s") ? "" : "s"}`))
    .join(" · ");
  const statusTone =
    summary.tone === "danger" || summary.tone === "warning" ? summary.tone : "success";
  return (
    <div
      className="not-prose my-0.5 flex w-[48rem] max-w-full min-w-0 flex-col gap-1 rounded-md border border-border bg-muted/30 px-3 py-2 text-sm"
      data-testid="factory-demo-card"
      data-status={summary.demoStatus}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-muted-foreground">
        <ClapperboardIcon className="size-3.5 shrink-0 text-brand-accent" aria-hidden />
        <span className="shrink-0 font-semibold text-foreground">Demo published</span>
        {summary.demoStatus && <KindPill label={summary.demoStatus} tone={statusTone} />}
        {summary.pr !== undefined && (
          <span className="shrink-0">
            PR #{summary.pr}
            {summary.sha && (
              <span className="font-mono tabular-nums"> @{summary.sha.slice(0, 7)}</span>
            )}
          </span>
        )}
        {href && (
          <span className="ml-auto">
            <RowLink to={href} label="Open review" testId="factory-demo-open" />
          </span>
        )}
      </div>
      {summary.demoTitle && (
        <p className="min-w-0 break-words text-ui font-medium text-foreground">
          {summary.demoTitle}
        </p>
      )}
      {summary.demoSummary && (
        <p className="min-w-0 break-words text-muted-foreground">{summary.demoSummary}</p>
      )}
      {fileText && <p className="text-muted-foreground">{fileText}</p>}
    </div>
  );
}

/**
 * One-line row for a factory tool call. Expands to the roster table or the
 * call's key facts; falls back to the plain error code when the call failed.
 */
export function FactoryToolLine({
  name,
  arguments: args,
  output,
  state,
}: {
  name: string;
  arguments: Record<string, unknown>;
  output: string | null;
  state: ToolState;
}) {
  const { factoryId, resolveSession } = useFactoryTranscriptEnv();
  const summary = useMemo(
    () => summarizeFactoryTool(name, args, output, state),
    [name, args, output, state],
  );
  if (!summary) return null;
  if (
    summary.tool === "sys_factory_demo" &&
    !summary.running &&
    !summary.error &&
    !summary.unsettled
  ) {
    return <DemoPublishedCard summary={summary} />;
  }
  const Icon = summary.running
    ? Loader2Icon
    : summary.tool === "sys_factory_env" && summary.pr === undefined
      ? ServerIcon
      : TOOL_ICON[summary.tool]!;
  const target = summary.targetSessionId
    ? (resolveSession(summary.targetSessionId)?.title ?? summary.targetSessionId.slice(0, 8))
    : null;
  const hasDetails =
    (summary.roster?.length ?? 0) > 0 || (summary.facts?.length ?? 0) > 0 || summary.error !== null;
  const links = rowLinks(summary, factoryId);
  const line = (
    <>
      <Icon
        className={cn(
          "size-3.5 shrink-0",
          summary.running ? "animate-spin text-info" : TONE_ICON[summary.tone],
        )}
        aria-hidden
      />
      <span className="min-w-0 truncate">
        <span className="font-semibold text-foreground">{summary.verb}</span>
        {summary.unsettled && (
          <>
            {" "}
            <KindPill
              label={summary.unsettled}
              tone="muted"
              className="h-4 px-1.5 align-text-bottom text-xs"
            />
          </>
        )}
        {target && <span> of {target}</span>}
        {summary.text && <span> {summary.text}</span>}
        {summary.error && <span className="text-destructive"> · {summary.error}</span>}
      </span>
    </>
  );
  return (
    <Collapsible
      defaultOpen={false}
      className="group/factory-line not-prose w-[48rem] max-w-full min-w-0"
      data-testid="factory-tool-line"
      data-tool={summary.tool}
      data-tone={summary.tone}
    >
      {/* The chevron follows the text (like a folded tool run); links sit at the
          right, and stay right-aligned when they wrap to their own line. */}
      <div className="flex w-full min-w-0 flex-wrap items-center gap-x-2 text-muted-foreground text-chat">
        {hasDetails ? (
          <CollapsibleTrigger className="flex max-w-full min-w-0 cursor-pointer items-center gap-1.5 py-0.5 text-left transition-colors hover:text-foreground">
            {line}
            <ChevronRightIcon className="size-3.5 shrink-0 transition-transform group-data-[state=open]/factory-line:rotate-90" />
          </CollapsibleTrigger>
        ) : (
          <div className="flex max-w-full min-w-0 items-center gap-1.5 py-0.5">{line}</div>
        )}
        {links.length > 0 && (
          <span className="ml-auto flex shrink-0 items-center gap-2">{links}</span>
        )}
      </div>
      {hasDetails && (
        <CollapsibleContent className="mt-1 ml-2 space-y-2 border-l pl-3 py-1">
          {summary.roster && summary.roster.length > 0 && <RosterTable rows={summary.roster} />}
          {summary.facts && summary.facts.length > 0 && <Facts facts={summary.facts} />}
          {summary.error && <p className="text-sm text-destructive">Error: {summary.error}</p>}
        </CollapsibleContent>
      )}
    </Collapsible>
  );
}
