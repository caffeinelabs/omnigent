/**
 * The Overview's first panel: everything waiting on a human, split into
 * Mine (assigned to or claimed by me), Role (for operators or owners, when I
 * am one) and All. One dense row per item with its next step: Review → for a
 * ready PR, the decision's options (the full card opens in place), an
 * incident's first runbook step and its agent (one click to the Agent page),
 * Open for an alert or a claim the gates don't back.
 */

import { type ReactNode, useState } from "react";
import {
  ChevronDownIcon,
  CircleCheckIcon,
  CircleHelpIcon,
  CircleXIcon,
  StarIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { RunbookButtons } from "../alerts/IssueActions";
import { SeverityIcon } from "../alerts/IssueCard";
import { issueMember } from "../alerts/runbookModel";
import { DecisionCard, answerBlockedReason, assigneeText } from "../decisions/DecisionCard";
import { formatElapsed, plural } from "../factoryFormat";
import {
  agentPageHref,
  alertPageHref,
  factoryPageHref,
  prPageHref,
  projectPageHref,
} from "../factoryRoutes";
import { useViewer, useViewerCan } from "../shell/viewerCan";
import {
  type NeedsItem,
  type NeedsTab,
  type NeedsYouSplit,
  defaultNeedsTab,
} from "./needsYouModel";

/** Rows shown before "Show all": keeps the projects table on the first screen. */
export const NEEDS_ROWS_SHOWN = 5;

const TABS: { key: NeedsTab; label: string }[] = [
  { key: "mine", label: "Mine" },
  { key: "role", label: "Role" },
  { key: "all", label: "All" },
];

/** Whether `item` is the decision `?decision=` names (an id or `D-n`). */
function isHighlighted(item: NeedsItem, highlight: string | null): boolean {
  return (
    highlight != null &&
    item.kind === "decision" &&
    (item.decision.id === highlight || item.decision.ref === highlight)
  );
}

function Row({
  item,
  snapshot,
  nowS,
  clockOffsetS,
  highlighted = false,
}: {
  item: NeedsItem;
  snapshot: FactorySnapshot;
  nowS: number;
  clockOffsetS: number;
  highlighted?: boolean;
}) {
  const viewer = useViewer();
  const { can } = useViewerCan();
  const [open, setOpen] = useState(highlighted);
  const fid = snapshot.factory.id;
  let icon = <CircleHelpIcon aria-hidden className="size-3.5 text-warning" />;
  let ref: string | null = null;
  let text: string;
  let meta: string | null = null;
  let action: ReactNode = null;
  let lead: ReactNode = null;
  switch (item.kind) {
    case "pr": {
      const { pr } = item;
      icon = <StarIcon aria-hidden className="size-3.5 fill-current text-success" />;
      ref = `!${pr.number}`;
      text = `${pr.project_slug ?? pr.repo} ready · ${pr.title}`;
      meta = pr.ready_since != null ? formatElapsed(nowS - pr.ready_since) : null;
      action = (
        <Button asChild size="xs" variant="outline">
          <Link
            to={prPageHref(fid, pr.id)}
            componentId="factory.overview.needs_review"
            data-testid={`needs-review-${pr.number}`}
          >
            Review →
          </Link>
        </Button>
      );
      break;
    }
    case "decision": {
      const { decision } = item;
      icon = decision.blocking ? (
        <TriangleAlertIcon aria-hidden className="size-3.5 text-warning" />
      ) : (
        <CircleHelpIcon aria-hidden className="size-3.5 text-muted-foreground" />
      );
      ref = decision.ref;
      const who = assigneeText(decision, viewer?.user_id ?? null);
      text = `${who ? `${who} · ` : ""}${decision.project_slug ? `${decision.project_slug}: ` : ""}${decision.question}`;
      meta = formatElapsed(nowS - decision.created_at);
      // Someone who may not answer still reads the card: the button says so.
      const readOnly = answerBlockedReason(decision, can, viewer?.user_id ?? null) != null;
      const recommended = readOnly ? null : decision.options.find((o) => o.recommended);
      action = (
        <Button
          size="xs"
          variant={open ? "secondary" : "outline"}
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          data-testid={`needs-decision-${decision.ref}`}
          componentId="factory.overview.needs_decision"
        >
          {open ? (
            "Close"
          ) : recommended ? (
            <>
              <span className="max-w-40 truncate max-sm:hidden">{recommended.label} ★</span>
              <span className="sm:hidden">Answer</span>
            </>
          ) : readOnly ? (
            "View"
          ) : (
            "Answer…"
          )}
          <ChevronDownIcon className={cn("transition-transform", open && "rotate-180")} />
        </Button>
      );
      break;
    }
    case "alert": {
      icon = <CircleXIcon aria-hidden className="size-3.5 text-destructive" />;
      text = item.alert.text;
      const slug = item.alert.targetId.replace(/^factory-project-/, "");
      const isProject = item.alert.targetId.startsWith("factory-project-");
      action = (
        <Button asChild size="xs" variant="outline">
          <Link
            to={isProject ? projectPageHref(fid, slug) : factoryPageHref(fid)}
            componentId="factory.overview.needs_alert"
          >
            Open
          </Link>
        </Button>
      );
      break;
    }
    case "issue": {
      const { issue } = item;
      const issueRef = issue.ref ?? issue.id;
      const member = issueMember(snapshot, issue);
      icon = <SeverityIcon issue={issue} />;
      ref = issue.ref;
      text = `${member ? "" : issue.target_title ? `${issue.target_title} · ` : ""}${issue.summary}`;
      lead = member ? (
        <Link
          to={agentPageHref(fid, member.session_id)}
          componentId="factory.overview.needs_issue_agent"
          data-testid={`needs-issue-agent-${issueRef}`}
          className="shrink-0 rounded-sm bg-muted px-1.5 py-0.5 font-mono text-xs hover:bg-muted/70 hover:underline"
        >
          {member.title}
        </Link>
      ) : null;
      meta = formatElapsed(nowS - issue.first_seen);
      action = (
        <span className="flex items-center gap-1">
          <RunbookButtons issue={issue} limit={1} />
          <Button asChild size="xs" variant="outline">
            <Link
              to={alertPageHref(fid, issueRef)}
              componentId="factory.overview.needs_issue"
              data-testid={`needs-issue-${issueRef}`}
            >
              Open
            </Link>
          </Button>
        </span>
      );
      break;
    }
    case "claim": {
      icon = <TriangleAlertIcon aria-hidden className="size-3.5 text-warning" />;
      text = `${item.project.slug}: the PL’s claim doesn’t match the gates`;
      action = (
        <Button asChild size="xs" variant="outline">
          <Link
            to={projectPageHref(fid, item.project.slug)}
            componentId="factory.overview.needs_claim"
          >
            Open
          </Link>
        </Button>
      );
      break;
    }
  }
  return (
    <li
      data-testid={`needs-item-${item.key}`}
      data-scope={item.scope}
      className="flex min-w-0 flex-col py-1.5 first:pt-0 last:pb-0"
    >
      <div className="flex min-w-0 items-center gap-2 text-sm">
        <span className="flex w-4 shrink-0 justify-center">{icon}</span>
        {ref && <span className="shrink-0 font-mono text-muted-foreground">{ref}</span>}
        {lead}
        {/* Beside an agent chip a phone has no room left: the text takes its own line. */}
        <span
          className={cn("min-w-0 flex-1 truncate", lead != null && "max-sm:hidden")}
          title={text}
        >
          {text}
        </span>
        {lead != null && <span className="flex-1 sm:hidden" />}
        {meta && (
          <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums max-sm:hidden">
            {meta}
          </span>
        )}
        <span className="shrink-0">{action}</span>
      </div>
      {lead != null && (
        <p
          data-testid={`needs-item-text-${item.key}`}
          className="line-clamp-2 pt-0.5 pl-6 text-sm break-words sm:hidden"
        >
          {text}
        </p>
      )}
      {open && item.kind === "decision" && (
        <div className="pt-2 pl-6">
          <DecisionCard
            decision={item.decision}
            clockOffsetS={clockOffsetS}
            highlighted={highlighted}
          />
        </div>
      )}
    </li>
  );
}

export function NeedsYouPanel({
  snapshot,
  split,
  nowS,
  clockOffsetS,
  highlightDecision = null,
}: {
  snapshot: FactorySnapshot;
  split: NeedsYouSplit;
  nowS: number;
  clockOffsetS: number;
  /** `?decision=` without the dock: that decision's row opens, in a tab that lists it. */
  highlightDecision?: string | null;
}) {
  const [picked, setPicked] = useState<NeedsTab | null>(null);
  const [showAll, setShowAll] = useState(false);
  const fallback = defaultNeedsTab(split);
  const lists = (key: NeedsTab) => split[key].some((i) => isHighlighted(i, highlightDecision));
  const tab = picked ?? (highlightDecision && !lists(fallback) && lists("all") ? "all" : fallback);
  const items = split[tab];
  const highlightAt = items.findIndex((i) => isHighlighted(i, highlightDecision));
  const total = split.all.length;
  return (
    <section
      id="needs-you"
      aria-label="Needs you"
      data-testid="factory-needs-you"
      data-count={split.mine.length + split.role.length}
      className="flex min-w-0 scroll-mt-24 flex-col gap-2.5 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
        <h2 className="text-ui font-semibold">Needs you</h2>
        <div
          role="tablist"
          aria-label="Whose"
          className="ml-auto inline-flex rounded-lg bg-muted p-[3px]"
        >
          {TABS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              onClick={() => setPicked(key)}
              data-testid={`needs-tab-${key}`}
              className={cn(
                "inline-flex h-6 cursor-pointer items-center gap-1 rounded-md px-2 text-sm font-medium transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                tab === key
                  ? "bg-background text-foreground shadow-sm dark:bg-input/30"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
              <span className="text-xs text-muted-foreground tabular-nums">
                {split[key].length}
              </span>
            </button>
          ))}
        </div>
      </div>
      {items.length === 0 ? (
        <p
          data-testid="needs-you-empty"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground"
        >
          <CircleCheckIcon aria-hidden className="size-3.5 text-success" />
          {total === 0
            ? "Nothing waits on anyone right now."
            : tab === "mine"
              ? `Nothing assigned to you · ${plural(total, "item")} for others`
              : `Nothing for your role · ${plural(total, "item")} in All`}
        </p>
      ) : (
        <ul data-testid="needs-you-list" className="flex flex-col divide-y divide-border">
          {(showAll || highlightAt >= NEEDS_ROWS_SHOWN
            ? items
            : items.slice(0, NEEDS_ROWS_SHOWN)
          ).map((item) => (
            <Row
              key={item.key}
              item={item}
              snapshot={snapshot}
              nowS={nowS}
              clockOffsetS={clockOffsetS}
              highlighted={isHighlighted(item, highlightDecision)}
            />
          ))}
        </ul>
      )}
      {items.length > NEEDS_ROWS_SHOWN && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          aria-expanded={showAll}
          data-testid="needs-you-more"
          className="-mt-1 self-start rounded-sm text-sm text-muted-foreground hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          {showAll ? "Show fewer" : `Show all ${items.length}`}
        </button>
      )}
    </section>
  );
}
