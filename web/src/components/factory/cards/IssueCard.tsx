/**
 * An incident (`INC-n`) or finding (`F-n`) as a Room card: severity, what it
 * is about, how often it fired, its status, Ack for an open incident and
 * Open → to its page.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowRightIcon,
  CheckIcon,
  Loader2Icon,
  OctagonAlertIcon,
  SearchCheckIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import type { IssueView } from "@/lib/factoryApi";
import { ackRoomIssue } from "@/lib/factoryRoomApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { ageText } from "../board/boardModel";
import { useFactoryServerNowS } from "../factoryClock";
import { TONE_CHIP, type FactoryTone } from "../factoryFormat";
import { improvementPageHref, incidentPageHref } from "../factoryRoutes";
import { ReasonGated, useViewerCan } from "../shell/viewerCan";

const SEVERITY_TONE: Record<IssueView["severity"], FactoryTone> = {
  info: "muted",
  notice: "active",
  warning: "warning",
  critical: "fail",
};

export function IssueCard({
  factoryId,
  issue,
  clockOffsetS = 0,
  className,
}: {
  factoryId: string;
  issue: IssueView;
  clockOffsetS?: number;
  className?: string;
}) {
  const queryClient = useQueryClient();
  const nowS = useFactoryServerNowS(clockOffsetS);
  const { gate } = useViewerCan();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = issue.ref ?? `#${issue.number}`;
  const incident = issue.class === "incident";
  const Icon = incident ? OctagonAlertIcon : SearchCheckIcon;
  const href = incident
    ? incidentPageHref(factoryId, issue.number)
    : improvementPageHref(factoryId, ref);
  const ack = async () => {
    setBusy(true);
    setError(null);
    try {
      await ackRoomIssue(factoryId, ref);
      for (const key of factoryInvalidationKeys(factoryId)) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <article
      data-testid={`issue-card-${ref}`}
      data-status={issue.status}
      className={cn(
        "flex min-w-0 flex-col gap-1.5 rounded-lg border border-border bg-card p-3 text-sm",
        className,
      )}
    >
      <header className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
        <Icon
          aria-hidden
          className={cn(
            "size-3.5 shrink-0",
            incident ? "text-destructive" : "text-muted-foreground",
          )}
        />
        <span className="font-mono font-medium">{ref}</span>
        <span
          className={cn(
            "rounded-full px-1.5 text-xs leading-5",
            TONE_CHIP[SEVERITY_TONE[issue.severity]],
          )}
        >
          {issue.severity}
        </span>
        {issue.count > 1 && <span className="text-muted-foreground">×{issue.count}</span>}
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">
          {issue.status} · {ageText(nowS - issue.first_seen)}
        </span>
      </header>
      <p className="break-words">{issue.summary}</p>
      {issue.target_title && <p className="text-muted-foreground">on {issue.target_title}</p>}
      <div className="flex flex-wrap items-center gap-1">
        {incident && issue.status === "open" && (
          <ReasonGated reason={gate("control").reason} testId="ack">
            <Button
              size="sm"
              variant="outline"
              onClick={() => void ack()}
              disabled={busy}
              data-testid={`issue-ack-${ref}`}
            >
              {busy ? <Loader2Icon className="animate-spin" /> : <CheckIcon />}
              Ack
            </Button>
          </ReasonGated>
        )}
        <Button asChild size="xs" variant="ghost" className="ml-auto text-muted-foreground">
          <Link to={href} componentId="factory.room.issue_open">
            Open
            <ArrowRightIcon className="size-3.5" />
          </Link>
        </Button>
      </div>
      {error && <p className="text-destructive">{error}</p>}
    </article>
  );
}
