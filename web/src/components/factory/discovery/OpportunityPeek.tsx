/**
 * One opportunity in the side panel (a bottom sheet on a phone): its status,
 * lifecycle and the need in users' words; why it is here (Evidence →
 * Opportunity → Bet → Project → Readout, missing links hatched); the evidence
 * meter and its reason, a bar per source, the hunches dashed, say against do;
 * what people said (verified excerpts, the hunches marked "not user
 * evidence", what argues against); the score with every part; the bet or
 * Scout's bet note; and people's votes and pins, which never change the
 * score. The footer holds the next step and every lever.
 */

import { ArrowBigUpIcon, CheckIcon, LinkIcon, PinIcon } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Button } from "@/components/ui/button";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { discoveryPageHref, projectPageHref } from "../factoryRoutes";
import { EvidenceMeter } from "../ui/EvidenceMeter";
import { Peek } from "../ui/Peek";
import { WhyChain, type WhyLink } from "../ui/WhyChain";
import { DATA_HUE, FAINT_TEXT, FOCUS_RING, MEANING } from "../ui/statusTokens";
import { AskScoutButton, ResearchForm } from "./AskScout";
import {
  HUNCH_WEIGHT_FALLBACK,
  type OpportunityDetail,
  type QuoteView,
  type ReadoutView,
  latestReadouts,
  leversFor,
  rankText,
  statusWords,
  weightText,
} from "./discoveryModel";
import { isNotFound, useOpportunity } from "./discoveryQueries";
import type { DiscoveryLevers } from "./useDiscoveryLevers";

const EYEBROW = "text-xs font-semibold tracking-wide text-muted-foreground uppercase";
const NO_READOUTS: readonly ReadoutView[] = [];

function Section({
  title,
  extra,
  children,
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mt-5 flex flex-col gap-2">
      <h3 className={cn(EYEBROW, "flex flex-wrap items-center gap-2")}>
        {title}
        {extra && <span className="font-normal tracking-normal normal-case">{extra}</span>}
      </h3>
      {children}
    </section>
  );
}

function day(at: number | null): string | null {
  if (at == null) return null;
  return new Date(at * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function ago(at: number | null, nowS: number): string | null {
  if (at == null) return null;
  const days = Math.max(0, Math.round((nowS - at) / 86400));
  return days === 0 ? "today" : days === 1 ? "1 day ago" : `${days} days ago`;
}

function Quote({
  quote,
  dashed = false,
  weight = HUNCH_WEIGHT_FALLBACK,
}: {
  quote: QuoteView;
  dashed?: boolean;
  weight?: number;
}) {
  return (
    <figure
      data-testid={`quote-${quote.signal}`}
      className={cn(
        "m-0 rounded-lg border px-3 py-2",
        dashed ? "border-dashed border-border" : "border-border bg-card",
      )}
    >
      <blockquote className="m-0 text-sm break-words">“{quote.text}”</blockquote>
      <figcaption className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
        {quote.hunch ? (
          <span>
            hunch{quote.author ? ` from ${quote.author}` : ""} · weight {weightText(weight)} · not
            user evidence
          </span>
        ) : (
          <>
            {quote.source && (
              <span className="rounded border border-border px-1 font-mono">{quote.source}</span>
            )}
            {quote.segments.length > 0 && <span>{quote.segments.join(", ")}</span>}
            {day(quote.observedAt) && <span>{day(quote.observedAt)}</span>}
            <span className="font-mono">{quote.signal}</span>
            <span className="grow" />
            {quote.verified && (
              <span className={cn("flex items-center gap-0.5", MEANING.working.text)}>
                <CheckIcon aria-hidden className="size-3" /> verified
              </span>
            )}
          </>
        )}
      </figcaption>
    </figure>
  );
}

function whyLinks(
  detail: OpportunityDetail,
  factoryId: string,
  onPeek: (value: string) => void,
): WhyLink[] {
  return detail.why.map((step): WhyLink => {
    switch (step.kind) {
      case "evidence":
        return {
          kind: "evidence",
          value: step.label || "none yet",
          detail:
            Object.entries(detail.evidence.bySource)
              .map(([k, n]) => `${k} ${n}`)
              .join(" · ") || undefined,
          missing: step.missing,
        };
      case "opportunity":
        return {
          kind: "opportunity",
          value: `${detail.id} · ${detail.evidence.level}`,
          detail: detail.rank != null ? `score ${rankText(detail.rank)}` : undefined,
        };
      case "bet":
        return {
          kind: "bet",
          value: step.ref
            ? `${step.ref}${detail.bet?.status === "pending" ? " · waits for you" : ""}`
            : "none yet",
          detail: step.ref ? step.label : "no metric or range",
          missing: step.missing,
          waiting: detail.bet?.status === "pending",
          onSelect: step.ref ? () => onPeek(step.ref!) : undefined,
        };
      case "project":
        return {
          kind: "project",
          value: step.ref ?? "none yet",
          detail: step.ref ? (detail.projects[0]?.stage ?? undefined) : undefined,
          missing: step.missing,
          to: step.ref ? projectPageHref(factoryId, step.ref) : undefined,
        };
      default:
        return {
          kind: "readout",
          value: step.missing ? "not yet" : step.label,
          detail: step.missing
            ? detail.bet
              ? "day 7 and 28 after release"
              : "not measured"
            : undefined,
          missing: step.missing,
        };
    }
  });
}

function Evidence({ detail, nowS }: { detail: OpportunityDetail; nowS: number }) {
  const ev = detail.evidence;
  const total = Object.values(ev.bySource).reduce((a, b) => a + b, 0) || 1;
  const say = ev.say + ev.do > 0 ? Math.round((ev.say / (ev.say + ev.do)) * 100) : null;
  const kinds = ev.sourceKinds.length;
  const facts = [
    `${ev.accounts} accounts`,
    `${kinds} source ${kinds === 1 ? "type" : "types"}`,
    ev.do > 0 ? "with behaviour data" : "no behaviour data yet",
    ev.lastSeen != null ? `newest ${ago(ev.lastSeen, nowS)}` : null,
  ].filter(Boolean);
  return (
    <Section
      title="Evidence"
      extra={
        <span className="flex items-center gap-2">
          {ev.level !== "none" && <EvidenceMeter level={ev.level} showLabel />}
          <span>computed by the server, never by an agent</span>
        </span>
      }
    >
      <p className="text-sm">{facts.join(" · ")}</p>
      {ev.reason && <p className="text-sm text-muted-foreground">{ev.reason}</p>}
      <ul className="m-0 flex list-none flex-col gap-1 p-0" aria-label="Signals per source">
        {Object.entries(ev.bySource).map(([tag, n]) => (
          <li
            key={tag}
            className="grid grid-cols-[3.5rem_minmax(0,1fr)_2rem] items-center gap-2 text-sm"
          >
            <span className="rounded border border-border px-1 text-center font-mono text-xs">
              {tag}
            </span>
            <span className="block h-1.5 overflow-hidden rounded-full bg-status-gray/20">
              <span
                className="block h-full rounded-full"
                style={{ width: `${(n / total) * 100}%`, background: DATA_HUE }}
              />
            </span>
            <span className="text-right font-mono tabular-nums">{n}</span>
          </li>
        ))}
        {ev.hunches > 0 && (
          <li className="grid grid-cols-[3.5rem_minmax(0,1fr)_2rem] items-center gap-2 text-sm">
            <span className="rounded border border-border px-1 text-center font-mono text-xs">
              team
            </span>
            <span
              className="block h-1.5 rounded-full border border-dashed border-border-strong"
              title="Hunches, not user evidence"
            />
            <span className="text-right font-mono tabular-nums">{ev.hunches}</span>
          </li>
        )}
      </ul>
      {say != null && (
        <div className="grid grid-cols-[3.5rem_minmax(0,1fr)] items-center gap-2 text-sm">
          <span className="text-muted-foreground">Say / do</span>
          <span className="flex min-w-0 items-center gap-2">
            <span className="block h-1.5 min-w-16 grow overflow-hidden rounded-full bg-status-gray/20">
              <span
                className="block h-full"
                style={{
                  width: `${say}%`,
                  background: DATA_HUE,
                }}
              />
            </span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {say} % say · {100 - say} % do{ev.do === 0 ? " (needs behaviour data)" : ""}
            </span>
          </span>
        </div>
      )}
      {ev.counter > 0 && (
        <p className="text-xs text-muted-foreground">
          {ev.counter} against (a third or more lowers the level by one)
        </p>
      )}
    </Section>
  );
}

function Score({ detail }: { detail: OpportunityDetail }) {
  if (detail.parts.length === 0) return null;
  return (
    <Section
      title={`Score ${rankText(detail.rank)}${detail.band ? ` · ${detail.band}` : ""}`}
      extra={detail.rubricVersion != null ? `definition v${detail.rubricVersion}` : undefined}
    >
      <div
        data-testid="opportunity-score"
        className="grid grid-cols-2 gap-2 sm:grid-cols-[repeat(4,minmax(0,1fr))_auto]"
      >
        {detail.parts.map((p, i) => (
          <div key={p.name} className="flex min-w-0 flex-col rounded-lg border border-border p-2">
            <span className="font-mono text-base tabular-nums">{rankText(p.value)}</span>
            <span className="text-xs font-semibold">
              {i > 0 ? "× " : ""}
              {p.name}
            </span>
            <span className="text-xs break-words text-muted-foreground">{p.input || p.reason}</span>
          </div>
        ))}
        <div className="flex items-center justify-center font-mono text-base font-semibold">
          = {rankText(detail.rank)}
        </div>
      </div>
      {detail.scoreLine && (
        <p className={cn("font-mono text-xs", FAINT_TEXT)}>{detail.scoreLine}</p>
      )}
    </Section>
  );
}

function Body({
  detail,
  factoryId,
  nowS,
  goalLabel,
  segmentLabel,
  onPeek,
  research,
  weight,
}: {
  detail: OpportunityDetail;
  factoryId: string;
  nowS: number;
  goalLabel: (id: string) => string | null;
  segmentLabel: (id: string) => string;
  onPeek: (value: string) => void;
  research: ReactNode;
  weight: number;
}) {
  const betRef = detail.proposals.find((p) => p.bet)?.proposal ?? detail.bet?.proposal ?? null;
  const said = [
    "written by Scout",
    detail.goal
      ? `under ${detail.goal}${goalLabel(detail.goal) ? ` ${goalLabel(detail.goal)}` : ""}`
      : "no goal yet (unassigned)",
    detail.segments.length ? `segment ${detail.segments.map(segmentLabel).join(", ")}` : null,
  ].filter(Boolean);
  return (
    <div data-testid={`opportunity-peek-${detail.id}`}>
      {detail.statement && (
        <figure className="m-0 rounded-lg border border-dashed border-border px-3 py-2">
          <blockquote className="m-0 text-sm">“{detail.statement}”</blockquote>
          <figcaption className="mt-1 text-xs text-muted-foreground">
            The need in users' words · {said.join(" · ")}
          </figcaption>
        </figure>
      )}
      {detail.nonGoal && (
        <p className="mt-3 rounded-lg border border-border px-3 py-2 text-sm">
          Parked under the non-goal “{detail.nonGoal}”. It never reaches the portfolio lead while
          the strategy says so.
        </p>
      )}
      {research}
      <Section title="Why it is here">
        <WhyChain
          links={whyLinks(detail, factoryId, onPeek)}
          compact
          label={`Why ${detail.id} is here`}
        />
      </Section>
      <Evidence detail={detail} nowS={nowS} />
      <Section title="What people said" extra="redacted · checked against the stored excerpt">
        {detail.quotes.said.length === 0 && detail.quotes.hunches.length === 0 ? (
          <p className="text-sm text-muted-foreground">No excerpt is stored for it.</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {detail.quotes.said.map((q) => (
              <Quote key={q.signal} quote={q} />
            ))}
            {detail.quotes.hunches.map((q) => (
              <Quote key={q.signal} quote={q} dashed weight={weight} />
            ))}
          </div>
        )}
        {detail.quotes.against.length > 0 && (
          <div className="flex flex-col gap-1.5 rounded-lg border border-border p-2">
            <p className="text-xs font-semibold">Against</p>
            {detail.quotes.against.map((q) => (
              <Quote key={q.signal} quote={q} />
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          Agents can cite quotes but never write them.
          {detail.quotes.total > detail.quotes.said.length
            ? ` ${detail.quotes.total} signals support it in all.`
            : ""}
        </p>
      </Section>
      <Score detail={detail} />
      {betRef ? (
        <Section title="Bet">
          <button
            type="button"
            onClick={() => onPeek(betRef)}
            data-testid={`opportunity-bet-${betRef}`}
            className={cn(
              "rounded-lg border px-3 py-2 text-left text-sm hover:bg-muted",
              detail.bet?.status === "pending" ? MEANING.needsYou.border : "border-border",
              FOCUS_RING,
            )}
          >
            <span className="font-medium">
              {detail.proposals.find((p) => p.proposal === betRef)?.title ?? betRef}
            </span>
            <span className="block text-xs text-muted-foreground">
              {betRef} ·{" "}
              {detail.bet?.status === "pending"
                ? "waits for you · the other lead wrote a case against"
                : detail.bet?.status === "awaiting_cosign"
                  ? "the other lead is co-signing"
                  : (detail.bet?.status ?? "filed")}
            </span>
          </button>
        </Section>
      ) : detail.betNote ? (
        <Section title="Scout's bet note">
          <div className="flex flex-col gap-1 text-sm">
            {detail.betNote.ideas.length > 0 && (
              <p>Ways to solve it: {detail.betNote.ideas.join(" · ")}</p>
            )}
            {detail.betNote.riskiest && (
              <p>
                <span className="text-muted-foreground">Riskiest assumption:</span>{" "}
                {detail.betNote.riskiest}
              </p>
            )}
            {detail.betNote.cheapest && (
              <p>
                <span className="text-muted-foreground">Cheapest test:</span>{" "}
                {detail.betNote.cheapest}
              </p>
            )}
          </div>
        </Section>
      ) : null}
      <Section title="People">
        <p className="text-sm">
          <ArrowBigUpIcon aria-hidden className="inline size-3.5" /> {detail.votes.up}{" "}
          {detail.votes.up === 1 ? "vote" : "votes"}
          {detail.votes.down ? ` · ${detail.votes.down} against` : ""} ·{" "}
          {detail.pinned
            ? `pinned${detail.pinnedBy ? ` by ${detail.pinnedBy}` : ""}`
            : "not pinned"}{" "}
          <span className="text-muted-foreground">(votes and pins never change the score)</span>
        </p>
        <span>
          <AskScoutButton factoryId={factoryId} discuss variant="ghost" />
        </span>
      </Section>
    </div>
  );
}

export function OpportunityPeek({
  factoryId,
  opportunity,
  nowS,
  canAct,
  perAsk,
  hunchWeight = HUNCH_WEIGHT_FALLBACK,
  readouts = NO_READOUTS,
  levers,
  goalLabel,
  segmentLabel,
  onPeek,
  onClose,
  onKill,
}: {
  factoryId: string;
  /** `O-n`; null keeps the panel closed. */
  opportunity: string | null;
  nowS: number;
  canAct: boolean;
  perAsk: number | null;
  /** The rubric's hunch weight. */
  hunchWeight?: number;
  /** The readouts the server wrote, for a measured opportunity's decision. */
  readouts?: readonly ReadoutView[];
  levers: DiscoveryLevers;
  goalLabel: (id: string) => string | null;
  segmentLabel: (id: string) => string;
  onPeek: (value: string) => void;
  onClose: () => void;
  onKill: (target: { id: string; title: string }) => void;
}) {
  const query = useOpportunity(factoryId, opportunity, opportunity != null);
  const [researching, setResearching] = useState(false);
  const detail = query.data ?? null;
  const missing = query.error != null && isNotFound(query.error);
  const status = detail ? statusWords(detail, nowS) : null;
  const allowed = detail ? leversFor(detail) : null;
  const copyLink = () => {
    if (!opportunity) return;
    const href = `${window.location.origin}${discoveryPageHref(factoryId)}?peek=${encodeURIComponent(opportunity)}`;
    void navigator.clipboard?.writeText(href);
  };

  const next = detail?.next;
  // Its latest readout: by its bet, else by the opportunity, else the why chain's.
  const readoutRef = (() => {
    if (!detail) return null;
    const bet = detail.bet?.proposal ?? detail.proposals.find((p) => p.bet)?.proposal ?? null;
    const mine = latestReadouts(
      readouts.filter((r) => (bet && r.proposal === bet) || r.opportunity === detail.id),
    ).sort((a, b) => (b.day ?? 0) - (a.day ?? 0));
    return mine[0]?.id ?? detail.why.find((w) => w.kind === "readout" && w.ref)?.ref ?? null;
  })();
  // Navigation is for everyone; the levers (plan, unpark, revive) only for operators.
  const main = (() => {
    if (!detail || !next) return null;
    switch (next.action) {
      case "decide":
        return readoutRef ? (
          <Button
            size="sm"
            onClick={() => onPeek(`readout:${readoutRef}`)}
            data-testid="peek-readout"
          >
            {canAct ? next.label : "Open the readout"}
          </Button>
        ) : null;
      case "open_project":
        return next.project ? (
          <Button asChild size="sm">
            <Link to={projectPageHref(factoryId, next.project)}>Open project</Link>
          </Button>
        ) : null;
      case "review_bet":
        return next.proposal ? (
          <Button size="sm" onClick={() => onPeek(next.proposal!)}>
            {next.label}
          </Button>
        ) : null;
      case "plan":
        return canAct ? (
          <Button size="sm" onClick={() => void levers.plan(detail)} data-testid="peek-plan">
            {next.label}
          </Button>
        ) : null;
      case "unpark":
        return canAct ? (
          <Button size="sm" onClick={() => void levers.unpark(detail)}>
            Unpark
          </Button>
        ) : null;
      case "revive":
        return canAct ? (
          <Button size="sm" onClick={() => void levers.revive(detail)}>
            Revive
          </Button>
        ) : null;
      case "open":
        return detail.mergedInto ? (
          <Button size="sm" onClick={() => onPeek(detail.mergedInto!)}>
            {next.label}
          </Button>
        ) : null;
      default:
        return null;
    }
  })();

  return (
    <Peek
      open={opportunity != null}
      onOpenChange={(open) => {
        if (!open) {
          setResearching(false);
          onClose();
        }
      }}
      title={detail?.title ?? opportunity ?? ""}
      description={
        detail && detail.lifecycle.length > 0 ? (
          <span>
            {detail.lifecycle.map((s, i) => (
              <span key={s.step}>
                {i > 0 && " › "}
                {s.current ? <b className="text-foreground">{s.step}</b> : s.step}
              </span>
            ))}
          </span>
        ) : undefined
      }
      headerExtra={
        <span className="flex shrink-0 items-center gap-1.5">
          {opportunity && (
            <span className="rounded border border-border px-1.5 font-mono text-xs">
              {opportunity}
            </span>
          )}
          {status && (
            <span
              className={cn(
                "rounded-md px-1.5 text-xs font-medium max-[760px]:hidden",
                status.waits ? cn(MEANING.needsYou.surface, MEANING.needsYou.text) : "bg-muted",
              )}
            >
              {status.text}
            </span>
          )}
          <button
            type="button"
            onClick={copyLink}
            aria-label="Copy link"
            className={cn(
              "grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-muted",
              FOCUS_RING,
            )}
          >
            <LinkIcon aria-hidden className="size-4" />
          </button>
        </span>
      }
      footer={
        detail && canAct ? (
          <>
            {main}
            {allowed?.research && !researching && (
              <Button size="sm" variant="outline" onClick={() => setResearching(true)}>
                Research this
              </Button>
            )}
            {allowed?.vote && (
              <Button
                size="sm"
                variant="outline"
                aria-pressed={detail.votes.mine > 0}
                aria-label={detail.votes.mine > 0 ? "Take back your vote" : "Vote"}
                onClick={() => void levers.vote(detail)}
              >
                <ArrowBigUpIcon
                  aria-hidden
                  className={cn(detail.votes.mine > 0 && "fill-current")}
                />
              </Button>
            )}
            {allowed?.vote && (
              <Button size="sm" variant="outline" onClick={() => void levers.pin(detail)}>
                <PinIcon aria-hidden />
                {detail.pinned ? "Unpin" : "Pin"}
              </Button>
            )}
            {allowed?.park && (
              <Button size="sm" variant="ghost" onClick={() => void levers.park(detail)}>
                Park
              </Button>
            )}
            {allowed?.kill && (
              <Button
                size="sm"
                variant="ghost"
                className={MEANING.broken.text}
                onClick={() => onKill(detail)}
                data-testid="peek-kill"
              >
                Kill…
              </Button>
            )}
          </>
        ) : detail ? (
          <>
            {main}
            <p className="text-sm text-muted-foreground">Ask an operator to change this.</p>
          </>
        ) : undefined
      }
    >
      {missing ? (
        <p role="alert" className="text-sm">
          {opportunity} is not in this factory. It may belong to another factory, or it was merged.
        </p>
      ) : query.error ? (
        <p role="alert" className="text-sm">
          Could not load {opportunity}:{" "}
          {query.error instanceof Error ? query.error.message : "error"}. Everything else on this
          page still works.
        </p>
      ) : !detail ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <div className="h-16 animate-pulse rounded-lg bg-muted" />
          <div className="h-24 animate-pulse rounded-lg bg-muted" />
          <div className="h-40 animate-pulse rounded-lg bg-muted" />
        </div>
      ) : (
        <Body
          detail={detail}
          factoryId={factoryId}
          nowS={nowS}
          goalLabel={goalLabel}
          segmentLabel={segmentLabel}
          onPeek={onPeek}
          weight={hunchWeight}
          research={
            researching ? (
              <div className="mt-3">
                <ResearchForm
                  opportunity={detail.id}
                  perAsk={perAsk}
                  onCancel={() => setResearching(false)}
                  onSend={async (question) => {
                    const ok = await levers.research(detail, question);
                    if (ok) setResearching(false);
                    return ok;
                  }}
                />
              </div>
            ) : detail.research ? (
              <p className="mt-3 rounded-lg border border-border px-3 py-2 text-sm">
                Scout is researching: “{detail.research.question}”
                {detail.research.by ? ` (asked by ${detail.research.by})` : ""}.
              </p>
            ) : null
          }
        />
      )}
    </Peek>
  );
}
