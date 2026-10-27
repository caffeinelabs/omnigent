/**
 * Why each gate reads as it does: one row per gate with the evidence behind
 * it (check counts, the review and verify markers at their sha with links,
 * the reviewer's session, the files touched with protected ones flagged),
 * then Sentinel's independent verdict and the human review so far.
 */

import type { ReactNode } from "react";
import {
  ChevronRightIcon,
  CircleCheckIcon,
  ExternalLinkIcon,
  ShieldCheckIcon,
  ShieldXIcon,
  UserCheckIcon,
} from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { GateState, MemberView, PrView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { ageText } from "../board/boardModel";
import {
  GATE_KEY_LABEL,
  type GateKey,
  STATE_ICON,
  STATE_ICON_TONE,
  gateState,
  shownGates,
} from "../board/GateChip";
import { plural, sameSha, shortSha } from "../factoryFormat";
import { PanelHeading } from "./reviewParts";
import { gateTally } from "./reviewModel";

function Out({ href, children, testId }: { href: string; children: ReactNode; testId?: string }) {
  if (!/^https?:/.test(href)) {
    return (
      <Link
        to={href}
        componentId="factory.review.evidence"
        data-testid={testId}
        className="inline-flex items-center gap-0.5 text-foreground underline-offset-2 hover:underline"
      >
        {children}
      </Link>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      data-testid={testId}
      className="inline-flex items-center gap-0.5 text-foreground underline-offset-2 hover:underline"
    >
      {children}
      <ExternalLinkIcon aria-hidden className="size-3" />
    </a>
  );
}

function AtSha({ sha, head }: { sha: string | null; head: string }) {
  if (!sha) return null;
  const current = sameSha(sha, head);
  return (
    <span className={cn("font-mono tabular-nums", !current && "text-warning")}>
      @{shortSha(sha)}
      {!current && " (old head)"}
    </span>
  );
}

/** A separator that stays on the line of the part before it. */
function Sep() {
  return <span aria-hidden>{"\u00a0· "}</span>;
}

/** A gate that passes although the snapshot carries no marker for it. */
const PASSED_NO_DETAILS = "Passed (no marker details)";

/** The reviewer agent on this PR's project, for "reviewer session ↗". */
function reviewerOf(pr: PrView, members: MemberView[]): MemberView | null {
  const reviewers = members.filter((m) => m.role === "reviewer" && m.project === pr.project_slug);
  return (
    reviewers.find((m) => pr.task != null && m.title.includes(pr.task)) ?? reviewers[0] ?? null
  );
}

function Detail({
  pr,
  gate,
  state,
  members,
  nowS,
}: {
  pr: PrView;
  gate: GateKey;
  state: GateState;
  members: MemberView[];
  nowS: number;
}) {
  const { evidence } = pr.gates;
  const passed = state === "pass";
  const when = (at: number | null) =>
    at != null ? (
      <>
        <Sep />
        {ageText(nowS - at)}
      </>
    ) : null;
  switch (gate) {
    case "draft":
      return pr.is_draft ? "Still a draft on GitHub" : "Marked ready on GitHub";
    case "checks": {
      const c = evidence.checks;
      if (!c) return passed ? PASSED_NO_DETAILS : "No checks reported yet";
      return (
        <>
          <span className="tabular-nums">
            {c.passing}/{c.total} passing
            {c.failing > 0 && `, ${c.failing} failing`}
            {c.pending > 0 && `, ${c.pending} pending`}
          </span>
          {c.failing_names.length > 0 && (
            <span className="text-destructive">
              <Sep />
              {c.failing_names.slice(0, 3).join(", ")}
              {c.failing_names.length > 3 && ` +${c.failing_names.length - 3}`}
            </span>
          )}
          {c.url && (
            <>
              <Sep />
              <Out href={c.url}>checks</Out>
            </>
          )}
        </>
      );
    }
    case "review": {
      const r = evidence.review;
      if (!r)
        return passed ? PASSED_NO_DETAILS : `No review marker for ${shortSha(pr.head_sha)} yet`;
      const reviewer = reviewerOf(pr, members);
      return (
        <>
          {r.verdict ?? "Reviewed"} <AtSha sha={r.sha} head={pr.head_sha} />
          {r.by && (
            <>
              <Sep />
              {r.by}
            </>
          )}
          {when(r.at)}
          {r.url && (
            <>
              <Sep />
              <Out href={r.url}>comment</Out>
            </>
          )}
          {reviewer && (
            <>
              <Sep />
              <Out href={reviewer.url} testId="review-evidence-reviewer">
                reviewer session
              </Out>
            </>
          )}
        </>
      );
    }
    case "verify": {
      const v = evidence.verify;
      if (!v) {
        if (state === "skip") return "No env needed (factory:no-env)";
        return passed ? PASSED_NO_DETAILS : `No verify marker for ${shortSha(pr.head_sha)} yet`;
      }
      return (
        <>
          {v.result ?? "Verified"} <AtSha sha={v.sha} head={pr.head_sha} />
          {v.env && (
            <>
              {" "}
              in <span className="font-mono break-words">{v.env}</span>
            </>
          )}
          {when(v.at)}
          {v.url && (
            <>
              <Sep />
              <Out href={v.url}>comment</Out>
            </>
          )}
        </>
      );
    }
    case "demo": {
      // The snapshot's demo stands in when the gate carries no demo marker.
      const d =
        evidence.demo ??
        (pr.demo
          ? { status: pr.demo.status, sha: pr.demo.head_sha, at: pr.demo.created_at }
          : null);
      if (!d) return passed ? PASSED_NO_DETAILS : `No demo of ${shortSha(pr.head_sha)} yet`;
      return (
        <>
          {d.status ?? "Recorded"} <AtSha sha={d.sha} head={pr.head_sha} />
          {when(d.at)}
        </>
      );
    }
    case "mergeable":
      return `GitHub says ${(evidence.mergeable ?? "unknown").toLowerCase()}`;
    case "protected_paths":
      return (
        <Collapsible>
          <CollapsibleTrigger className="group inline-flex cursor-pointer items-center gap-1 rounded-sm hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none">
            {plural(evidence.files.length, "file")} ·{" "}
            {evidence.protected.length > 0 ? (
              <span className="text-destructive">{evidence.protected.length} protected</span>
            ) : (
              "none protected"
            )}
            <ChevronRightIcon
              aria-hidden
              className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
            />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul data-testid="review-evidence-files" className="mt-1 flex flex-col font-mono">
              {evidence.files.map((file) => {
                const isProtected = evidence.protected.includes(file);
                return (
                  <li
                    key={file}
                    data-protected={isProtected ? "true" : undefined}
                    className={cn(
                      "break-all",
                      isProtected && "rounded-sm bg-destructive/10 px-1 text-destructive",
                    )}
                  >
                    {file}
                  </li>
                );
              })}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      );
  }
}

function GateRow({
  pr,
  gate,
  members,
  nowS,
}: {
  pr: PrView;
  gate: GateKey;
  members: MemberView[];
  nowS: number;
}) {
  const state = gateState(pr, gate) ?? "unknown";
  const Icon = STATE_ICON[state];
  return (
    <li
      data-testid={`review-evidence-${gate}`}
      data-state={state}
      className="grid grid-cols-[0.875rem_4.75rem_minmax(0,1fr)] items-start gap-x-2 text-sm"
    >
      <Icon aria-hidden className={cn("mt-0.5 size-3.5", STATE_ICON_TONE[state])} />
      <span className="text-foreground">{GATE_KEY_LABEL[gate]}</span>
      <span className="min-w-0 text-muted-foreground">
        <Detail pr={pr} gate={gate} state={state} members={members} nowS={nowS} />
      </span>
    </li>
  );
}

function SentinelRow({ pr, nowS }: { pr: PrView; nowS: number }) {
  const s = pr.sentinel;
  const verified = s?.verdict === "verified";
  const current = sameSha(s?.head_sha, pr.head_sha);
  const Icon = verified ? ShieldCheckIcon : ShieldXIcon;
  return (
    <li
      data-testid="review-evidence-sentinel"
      data-state={!s ? "none" : verified && current ? "pass" : verified ? "old" : "fail"}
      className="grid grid-cols-[0.875rem_4.75rem_minmax(0,1fr)] items-start gap-x-2 text-sm"
    >
      <Icon
        aria-hidden
        className={cn(
          "mt-0.5 size-3.5",
          !s
            ? "text-muted-foreground"
            : verified && current
              ? "text-success"
              : verified
                ? "text-warning"
                : "text-destructive",
        )}
      />
      <span className="text-foreground">Sentinel</span>
      <span className="min-w-0 text-muted-foreground">
        {!s ? (
          "Not checked yet"
        ) : (
          <>
            {verified ? "verified" : "not verified"} <AtSha sha={s.head_sha} head={pr.head_sha} />
            {s.at != null && (
              <>
                <Sep />
                {ageText(nowS - s.at)}
              </>
            )}
            {s.note && <span className="block text-foreground/80">“{s.note}”</span>}
          </>
        )}
      </span>
    </li>
  );
}

function HumanRow({ pr, nowS }: { pr: PrView; nowS: number }) {
  const review = pr.human_review;
  if (!review) return null;
  const approved = review.verdict === "approved";
  return (
    <li
      data-testid="review-evidence-human"
      className="grid grid-cols-[0.875rem_4.75rem_minmax(0,1fr)] items-start gap-x-2 text-sm"
    >
      <UserCheckIcon
        aria-hidden
        className={cn("mt-0.5 size-3.5", approved ? "text-success" : "text-warning")}
      />
      <span className="text-foreground">Human</span>
      <span className="min-w-0 text-muted-foreground">
        {approved ? "Approved" : "Changes requested"} by {review.by}{" "}
        <AtSha sha={review.head_sha} head={pr.head_sha} />
        <Sep />
        {ageText(nowS - review.at)}
        {review.text && <span className="block text-foreground/80">“{review.text}”</span>}
      </span>
    </li>
  );
}

export function EvidencePanel({
  pr,
  members,
  nowS,
}: {
  pr: PrView;
  members: MemberView[];
  nowS: number;
}) {
  const tally = gateTally(pr);
  return (
    <section data-testid="review-evidence" className="flex min-w-0 flex-col gap-2">
      <PanelHeading
        title="Gates & evidence"
        meta={
          <span className={cn("tabular-nums", tally.passing === tally.total && "text-success")}>
            {tally.passing === tally.total && (
              <CircleCheckIcon aria-hidden className="mr-1 inline size-3.5 align-[-2px]" />
            )}
            {tally.passing}/{tally.total} passing
          </span>
        }
      />
      {pr.poll_error && (
        <p className="text-sm text-muted-foreground">
          GitHub can’t be read right now ({pr.poll_error}); these are the last known values.
        </p>
      )}
      <ul className="flex flex-col gap-1.5">
        {shownGates(pr).map((gate) => (
          <GateRow key={gate} pr={pr} gate={gate} members={members} nowS={nowS} />
        ))}
        <SentinelRow pr={pr} nowS={nowS} />
        <HumanRow pr={pr} nowS={nowS} />
      </ul>
    </section>
  );
}
