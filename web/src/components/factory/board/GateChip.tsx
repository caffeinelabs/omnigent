/**
 * One server-computed gate of a PR as a chip (`✓ CI 14/14`), with its
 * evidence (counts, marker sha, links) in a hover card. A live change of the
 * gate cross-fades the chip and pulses a ring once.
 */

import type { ReactNode } from "react";
import {
  CircleCheckIcon,
  CircleDotIcon,
  CircleHelpIcon,
  CircleMinusIcon,
  CircleXIcon,
  ExternalLinkIcon,
} from "lucide-react";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import type { GateState, PrView, ReadyGate } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { GATE_LABEL, shortSha } from "../factoryFormat";
import { useChangedSinceMount } from "./boardHooks";
import { ageText } from "./boardModel";

export type GateKey = ReadyGate | "demo";

/** Chip order on a PR row; `demo` only shows when the project records demos. */
export const GATE_ORDER: readonly GateKey[] = [
  "draft",
  "checks",
  "review",
  "verify",
  "demo",
  "mergeable",
  "protected_paths",
];

export const GATE_KEY_LABEL: Record<GateKey, string> = { ...GATE_LABEL, demo: "Demo" };

export const STATE_WORD: Record<GateState, string> = {
  pass: "passed",
  pending: "pending",
  fail: "failed",
  skip: "not needed",
  unknown: "unknown",
};

export const STATE_ICON: Record<GateState, typeof CircleCheckIcon> = {
  pass: CircleCheckIcon,
  pending: CircleDotIcon,
  fail: CircleXIcon,
  skip: CircleMinusIcon,
  unknown: CircleHelpIcon,
};

// Passing gates stay quiet (neutral pill, green glyph); attention states tint.
const STATE_CHIP: Record<GateState, string> = {
  pass: "border-border text-foreground",
  pending: "border-warning/30 bg-warning/10 text-warning",
  fail: "border-destructive/30 bg-destructive/10 text-destructive",
  skip: "border-border text-muted-foreground",
  unknown: "border-dashed border-muted-foreground/50 text-muted-foreground",
};

export const STATE_ICON_TONE: Record<GateState, string> = {
  pass: "text-success",
  pending: "text-warning",
  fail: "text-destructive",
  skip: "text-muted-foreground",
  unknown: "text-muted-foreground",
};

/** The gate's value, `unknown` while the PR's GitHub poll is failing. */
export function gateState(pr: PrView, gate: GateKey): GateState | null {
  const state = gate === "demo" ? pr.gates.demo : pr.gates[gate];
  if (state == null) return null;
  return pr.poll_error && state !== "skip" ? "unknown" : state;
}

/** Gates a PR row shows, in order (the demo gate only when it applies). */
export function shownGates(pr: PrView): GateKey[] {
  return GATE_ORDER.filter((gate) => gate !== "demo" || pr.gates.demo != null);
}

function chipText(pr: PrView, gate: GateKey): string {
  const checks = pr.gates.evidence.checks;
  if (gate === "checks" && checks && checks.total > 0) {
    return `${GATE_KEY_LABEL.checks} ${checks.passing}/${checks.total}`;
  }
  return GATE_KEY_LABEL[gate];
}

const REASON_MATCH: Record<GateKey, RegExp> = {
  draft: /draft/i,
  checks: /check|ci\b/i,
  review: /review/i,
  verify: /verify/i,
  demo: /demo/i,
  mergeable: /merge|conflict/i,
  protected_paths: /protected|path|scope/i,
};

function EvidenceLink({ href, children }: { href: string; children: ReactNode }) {
  const external = /^https?:/.test(href);
  if (!external) {
    return (
      <Link
        to={href}
        componentId="factory.gate.evidence"
        className="inline-flex items-center gap-1 text-foreground underline-offset-2 hover:underline"
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
      className="inline-flex items-center gap-1 text-foreground underline-offset-2 hover:underline"
    >
      {children}
      <ExternalLinkIcon aria-hidden className="size-3" />
    </a>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

function Sha({ sha, head }: { sha: string | null; head: string }) {
  if (!sha) return null;
  return (
    <span className={cn("font-mono tabular-nums", sha !== head && "text-warning")}>
      @{shortSha(sha)}
      {sha !== head && " (old head)"}
    </span>
  );
}

/** What backs a gate's value: counts, the marker's sha and links. */
function GateEvidence({ pr, gate, nowS }: { pr: PrView; gate: GateKey; nowS: number }) {
  const { evidence } = pr.gates;
  const when = (at: number | null) => (at != null ? ` · ${ageText(nowS - at)}` : "");
  switch (gate) {
    case "checks": {
      const c = evidence.checks;
      if (!c) return <Row>No checks reported yet.</Row>;
      return (
        <>
          <Row>
            <span className="tabular-nums">
              {c.passing} passing · {c.failing} failing · {c.pending} pending of {c.total}
            </span>
          </Row>
          {c.failing_names.length > 0 && (
            <ul className="text-sm text-destructive">
              {c.failing_names.slice(0, 6).map((name) => (
                <li key={name} className="flex min-w-0 items-center gap-1">
                  <CircleXIcon aria-hidden className="size-3 shrink-0" />
                  <span className="truncate">{name}</span>
                </li>
              ))}
            </ul>
          )}
          {c.url && <EvidenceLink href={c.url}>Checks on GitHub</EvidenceLink>}
        </>
      );
    }
    case "review": {
      const r = evidence.review;
      if (!r) return <Row>No review marker for {shortSha(pr.head_sha)} yet.</Row>;
      return (
        <>
          <Row>
            {r.verdict ?? "Reviewed"} <Sha sha={r.sha} head={pr.head_sha} />
            {r.by ? ` · ${r.by}` : ""}
            {when(r.at)}
          </Row>
          {r.url && <EvidenceLink href={r.url}>Review comment</EvidenceLink>}
        </>
      );
    }
    case "verify": {
      const v = evidence.verify;
      if (!v) {
        return (
          <Row>
            {pr.gates.verify === "skip"
              ? "No env needed (factory:no-env)."
              : `No verify marker for ${shortSha(pr.head_sha)} yet.`}
          </Row>
        );
      }
      return (
        <>
          <Row>
            {v.result ?? "Verified"} <Sha sha={v.sha} head={pr.head_sha} />
            {v.env ? (
              <>
                {" "}
                in <span className="font-mono">{v.env}</span>
              </>
            ) : null}
            {when(v.at)}
          </Row>
          {v.url && <EvidenceLink href={v.url}>Verify comment</EvidenceLink>}
        </>
      );
    }
    case "demo": {
      const d = evidence.demo;
      if (!d) return <Row>No demo of {shortSha(pr.head_sha)} yet.</Row>;
      return (
        <>
          <Row>
            {d.status ?? "Recorded"} <Sha sha={d.sha} head={pr.head_sha} />
            {when(d.at)}
          </Row>
          {d.url && <EvidenceLink href={d.url}>Watch the demo</EvidenceLink>}
        </>
      );
    }
    case "mergeable":
      return <Row>GitHub says {evidence.mergeable ?? "unknown"}.</Row>;
    case "protected_paths":
      return (
        <>
          <Row>
            {evidence.files.length} files ·{" "}
            {evidence.protected.length > 0
              ? `${evidence.protected.length} protected`
              : "none protected"}
          </Row>
          {evidence.files.length > 0 && (
            <ul className="font-mono text-sm text-muted-foreground">
              {evidence.files.slice(0, 8).map((file) => (
                <li
                  key={file}
                  className={cn(
                    "truncate",
                    evidence.protected.includes(file) && "text-destructive",
                  )}
                  title={file}
                >
                  {file}
                </li>
              ))}
              {evidence.files.length > 8 && <li>… {evidence.files.length - 8} more</li>}
            </ul>
          )}
        </>
      );
    case "draft":
      return <Row>{pr.is_draft ? "Still a draft on GitHub." : "Marked ready on GitHub."}</Row>;
  }
}

/** One gate chip; hover or focus shows its evidence. */
export function GateChip({
  pr,
  gate,
  nowS,
  className,
}: {
  pr: PrView;
  gate: GateKey;
  /** The clock evidence ages are measured against (epoch seconds). */
  nowS: number;
  className?: string;
}) {
  const state = gateState(pr, gate) ?? "unknown";
  const changed = useChangedSinceMount(state);
  const Icon = STATE_ICON[state];
  const reasons = pr.gates.reasons.filter((r) => REASON_MATCH[gate].test(r));
  const label = `${GATE_KEY_LABEL[gate]} ${STATE_WORD[state]}`;
  return (
    <HoverCard openDelay={150} closeDelay={100}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          data-testid={`gate-${gate}-${pr.number}`}
          data-state={state}
          aria-label={label}
          className={cn(
            "relative inline-flex h-5 min-w-0 cursor-default items-center gap-1 rounded-full border px-1.5 text-sm whitespace-nowrap tabular-nums transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
            STATE_CHIP[state],
            className,
          )}
        >
          {changed !== null && (
            <span
              key={`ring-${changed}`}
              aria-hidden
              className="pointer-events-none absolute -inset-px animate-[user-msg-flash_600ms_ease-out] rounded-full motion-reduce:hidden"
            />
          )}
          <span
            key={changed ?? "initial"}
            className={cn(
              "inline-flex min-w-0 items-center gap-1",
              changed !== null && "animate-in duration-600 fade-in-0 motion-reduce:animate-none",
            )}
          >
            <Icon aria-hidden className={cn("size-3 shrink-0", STATE_ICON_TONE[state])} />
            <span className="truncate">{chipText(pr, gate)}</span>
          </span>
        </button>
      </HoverCardTrigger>
      <HoverCardContent
        side="bottom"
        align="start"
        className="flex w-72 flex-col gap-1.5 bg-card-solid"
        data-testid={`gate-evidence-${gate}-${pr.number}`}
      >
        <p className="flex items-center gap-1.5 text-ui font-medium">
          <Icon aria-hidden className={cn("size-3.5", STATE_ICON_TONE[state])} />
          {label}
        </p>
        {pr.poll_error && (
          <p className="text-sm text-muted-foreground">
            GitHub can’t be read right now ({pr.poll_error}); this is the last known value.
          </p>
        )}
        <GateEvidence pr={pr} gate={gate} nowS={nowS} />
        {state !== "pass" &&
          reasons.map((reason) => (
            <p key={reason} className="text-sm text-muted-foreground">
              {reason}
            </p>
          ))}
      </HoverCardContent>
    </HoverCard>
  );
}
