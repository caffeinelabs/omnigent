/**
 * "Report a problem" on a card or an agent: anyone who reads the factory
 * files an incident (something is wrong now) or a finding (a process gap) as
 * themselves, with what it is about. It lands on Alerts and in the next
 * retro. At most 10 reports per person per hour (the server says when).
 */

import { useState } from "react";
import { FlagIcon } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { showToast } from "@/components/ui/toast";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import { type ReportIssueRequest, reportFactoryIssue } from "@/lib/factoryApi";
import { useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { controlErrorMessage } from "../controls/controlModel";
import { alertPageHref } from "../factoryRoutes";

export const SUMMARY_MAX = 200;

export function ReportProblemDialog({
  factoryId,
  open,
  onOpenChange,
  target,
  targetLabel,
  project,
}: {
  factoryId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** What it is about: `member:<sid>`, `pr:<id>`, `project:<slug>`, `decision:<id>`. */
  target: string;
  targetLabel: string;
  project?: string | null;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [summary, setSummary] = useState("");
  const [body, setBody] = useState("");
  const [cls, setCls] = useState<NonNullable<ReportIssueRequest["class"]>>("incident");
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const submit = async () => {
    const text = summary.trim();
    if (!text || sending) return;
    setSending(true);
    setFailure(null);
    try {
      const { issue } = await reportFactoryIssue(factoryId, {
        summary: text,
        ...(body.trim() ? { body: body.trim() } : {}),
        target,
        class: cls,
        severity: cls === "incident" ? "warning" : "notice",
        ...(project ? { project } : {}),
      });
      for (const key of factoryInvalidationKeys(factoryId, ["issues"])) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
      onOpenChange(false);
      setSummary("");
      setBody("");
      const ref = issue.ref ?? issue.id;
      showToast(
        <span data-testid="report-problem-toast">
          Reported as{" "}
          <button
            type="button"
            className="cursor-pointer font-mono underline"
            onClick={() => navigate(alertPageHref(factoryId, ref))}
          >
            {ref}
          </button>
        </span>,
      );
    } catch (error) {
      setFailure(controlErrorMessage(error));
    } finally {
      setSending(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="report-problem-dialog"
        className="flex flex-col gap-4 sm:max-w-md"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FlagIcon aria-hidden className="size-4 text-muted-foreground" />
            Report a problem
          </DialogTitle>
          <DialogDescription>
            About <span className="font-medium text-foreground">{targetLabel}</span>. It goes to
            Alerts as you, and into the next retro.
          </DialogDescription>
        </DialogHeader>
        <div
          role="radiogroup"
          aria-label="Kind"
          className="inline-flex self-start rounded-lg bg-muted p-[3px]"
        >
          {(["incident", "finding"] as const).map((c) => (
            <button
              key={c}
              type="button"
              role="radio"
              aria-checked={cls === c}
              data-testid={`report-problem-${c}`}
              onClick={() => setCls(c)}
              className={cn(
                "h-6 cursor-pointer rounded-md px-2 text-sm font-medium",
                cls === c
                  ? "bg-background text-foreground shadow-sm dark:bg-input/30"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {c === "incident" ? "Wrong now" : "Process gap"}
            </button>
          ))}
        </div>
        <Input
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          maxLength={SUMMARY_MAX}
          placeholder="What's wrong, in one line"
          aria-label="Summary"
          autoFocus
          data-testid="report-problem-summary"
          componentId="factory.report.summary"
        />
        <Textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Details (optional): what you expected, what happened"
          aria-label="Details"
          rows={3}
          data-testid="report-problem-body"
          componentId="factory.report.body"
        />
        {failure && (
          <p role="alert" data-testid="report-problem-error" className="text-sm text-destructive">
            {failure}
          </p>
        )}
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            componentId="factory.report.cancel"
          >
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            loading={sending}
            disabled={!summary.trim()}
            data-testid="report-problem-submit"
            componentId="factory.report.submit"
          >
            Report
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** A quiet "Report a problem" button that opens the dialog. */
export function ReportProblemButton({
  factoryId,
  target,
  targetLabel,
  project,
  size = "xs",
  testId = "report-problem",
  className,
}: {
  factoryId: string;
  target: string;
  targetLabel: string;
  project?: string | null;
  size?: "xs" | "sm";
  testId?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        size={size}
        variant="ghost"
        onClick={() => setOpen(true)}
        data-testid={testId}
        componentId="factory.report.open"
        className={cn("text-muted-foreground", className)}
      >
        <FlagIcon />
        Report a problem
      </Button>
      {open && (
        <ReportProblemDialog
          factoryId={factoryId}
          open={open}
          onOpenChange={setOpen}
          target={target}
          targetLabel={targetLabel}
          project={project}
        />
      )}
    </>
  );
}
