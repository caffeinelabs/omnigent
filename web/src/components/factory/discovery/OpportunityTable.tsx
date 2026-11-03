/**
 * The ranked opportunities, grouped by band (Now, Next, Later, Parked; the
 * cut-offs are rubric fields and shown in each band's header): rank, the
 * opportunity in users' words, evidence, source mix, score and what happens
 * next. Triage sits on each row (on hover, focus or selection): Vote, Plan,
 * Park, Kill; the keys `j`/`k`, Enter, `v`, `n`, `p`, `x` do the same on the
 * selected row. On a phone the evidence and source columns drop and the row
 * opens its panel, where every lever lives.
 */

import { ArrowBigUpIcon, PinIcon, XIcon } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { EvidenceMeter } from "../ui/EvidenceMeter";
import { type SourceCount, SourceMix } from "../ui/SourceMix";
import { FAINT_TEXT, FOCUS_RING, MEANING } from "../ui/statusTokens";
import {
  type BandSection,
  type OpportunityRow,
  leversFor,
  rankText,
  statusWords,
} from "./discoveryModel";

export interface TriageHandlers {
  open: (row: OpportunityRow) => void;
  vote: (row: OpportunityRow) => void;
  plan: (row: OpportunityRow) => void;
  park: (row: OpportunityRow) => void;
  kill: (row: OpportunityRow) => void;
}

function Status({
  row,
  nowS,
  phone = false,
}: {
  row: OpportunityRow;
  nowS: number;
  phone?: boolean;
}) {
  const words = statusWords(row, nowS);
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      {words.waits ? (
        <span
          data-testid={phone ? undefined : `opportunity-waits-${row.id}`}
          className={cn(
            "w-fit rounded-md px-1.5 text-sm font-medium",
            MEANING.needsYou.surface,
            MEANING.needsYou.text,
          )}
        >
          {words.text}
        </span>
      ) : (
        <span className="text-sm">{words.text}</span>
      )}
      {words.detail && <span className="text-xs text-muted-foreground">{words.detail}</span>}
    </div>
  );
}

function Row({
  row,
  index,
  selected,
  nowS,
  canAct,
  handlers,
  onSelect,
}: {
  row: OpportunityRow;
  index: number;
  selected: boolean;
  nowS: number;
  canAct: boolean;
  handlers: TriageHandlers;
  onSelect: (id: string) => void;
}) {
  const levers = leversFor(row);
  const sources: SourceCount[] = Object.entries(row.evidence.bySource).map(([tag, count]) => ({
    tag,
    count,
  }));
  if (row.evidence.hunches) sources.push({ tag: "team", count: row.evidence.hunches, hunch: true });
  return (
    <tr
      data-testid={`opportunity-row-${row.id}`}
      data-selected={selected || undefined}
      aria-selected={selected}
      onFocusCapture={() => onSelect(row.id)}
      onClick={() => onSelect(row.id)}
      className={cn(
        "group border-t border-border align-top",
        selected ? "bg-status-blue/10" : "hover:bg-muted/50",
        row.stale && "text-muted-foreground",
      )}
    >
      <td className="w-8 py-2.5 pl-3 font-mono text-sm text-muted-foreground tabular-nums">
        {index + 1}
      </td>
      <td className="min-w-0 py-2.5 pr-3">
        <button
          type="button"
          onClick={() => handlers.open(row)}
          data-testid={`opportunity-open-${row.id}`}
          className={cn("rounded text-left font-medium hover:underline", FOCUS_RING)}
        >
          {row.title}
        </button>
        {row.pinned && (
          <PinIcon
            aria-label="Pinned (never changes the score)"
            className={cn("ml-1 inline size-3", FAINT_TEXT)}
          />
        )}
        {row.statement && (
          <p className="line-clamp-1 text-sm text-muted-foreground" title={row.statement}>
            {row.statement}
          </p>
        )}
        <span className={cn("font-mono text-xs", FAINT_TEXT)}>{row.id}</span>
        <div className="mt-1 md:hidden">
          <Status row={row} nowS={nowS} phone />
        </div>
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap max-md:hidden">
        {row.evidence.level !== "none" ? (
          <EvidenceMeter level={row.evidence.level} showLabel />
        ) : (
          <span className={cn("text-sm", FAINT_TEXT)}>no evidence</span>
        )}
        <p className="text-xs text-muted-foreground">
          {row.evidence.accounts} accounts
          {row.evidence.counter ? ` · ${row.evidence.counter} against` : ""}
        </p>
      </td>
      <td className="py-2.5 pr-3 max-lg:hidden">
        <SourceMix sources={sources} />
        <p className="mt-1 font-mono text-xs text-muted-foreground">
          {[...Object.keys(row.evidence.bySource), ...(row.evidence.hunches ? ["hunch"] : [])].join(
            " · ",
          ) || "—"}
        </p>
      </td>
      <td className="py-2.5 pr-3 text-right font-mono text-sm tabular-nums">
        {row.band === "parked" || row.band == null ? (
          <span className={FAINT_TEXT}>—</span>
        ) : (
          rankText(row.rank)
        )}
      </td>
      <td className="py-2.5 pr-3 max-md:hidden">
        <Status row={row} nowS={nowS} />
      </td>
      <td className="w-[11.5rem] py-2 pr-2 max-md:hidden">
        {canAct && (
          <div
            className={cn(
              "flex justify-end gap-0.5 transition-opacity",
              selected
                ? "opacity-100"
                : "opacity-0 group-focus-within:opacity-100 group-hover:opacity-100",
            )}
          >
            {levers.vote && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-pressed={row.votes.mine > 0}
                aria-label={`Vote for ${row.title}. Votes never change the score.`}
                title="Vote (v). Votes never change the score."
                onClick={(e) => {
                  e.stopPropagation();
                  handlers.vote(row);
                }}
                data-testid={`opportunity-vote-${row.id}`}
              >
                <ArrowBigUpIcon aria-hidden className={cn(row.votes.mine > 0 && "fill-current")} />
                {row.votes.up || ""}
              </Button>
            )}
            {levers.plan && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={`Plan ${row.title}`}
                title="Ask the portfolio lead to plan it (n)"
                onClick={(e) => {
                  e.stopPropagation();
                  handlers.plan(row);
                }}
                data-testid={`opportunity-plan-${row.id}`}
              >
                Plan
              </Button>
            )}
            {levers.park && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={`Park ${row.title}`}
                title="Park it (p)"
                onClick={(e) => {
                  e.stopPropagation();
                  handlers.park(row);
                }}
                data-testid={`opportunity-park-${row.id}`}
              >
                Park
              </Button>
            )}
            {levers.kill && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={`Kill ${row.title}`}
                title="Kill with a reason (x)"
                onClick={(e) => {
                  e.stopPropagation();
                  handlers.kill(row);
                }}
                data-testid={`opportunity-kill-${row.id}`}
              >
                <XIcon aria-hidden />
              </Button>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

export function OpportunityTable({
  sections,
  selectedId,
  nowS,
  canAct,
  handlers,
  onSelect,
  footer,
}: {
  sections: readonly BandSection[];
  selectedId: string | null;
  nowS: number;
  /** The viewer may pull levers (operators); viewers only read. */
  canAct: boolean;
  handlers: TriageHandlers;
  onSelect: (id: string) => void;
  footer?: ReactNode;
}) {
  let index = 0;
  return (
    <section
      aria-label="Opportunities"
      data-testid="opportunity-table"
      className="min-w-0 overflow-hidden rounded-xl border border-border bg-card"
    >
      <table className="w-full table-fixed border-collapse text-left max-md:table-auto">
        <caption className="sr-only">
          Opportunities by band. Keys: j and k move, Enter opens, v votes, n plans, p parks, x
          kills.
        </caption>
        <colgroup>
          <col className="w-9" />
          <col />
          <col className="w-36 max-md:hidden" />
          <col className="w-36 max-lg:hidden" />
          <col className="w-16" />
          <col className="w-44 max-md:hidden" />
          <col className="w-[11.5rem] max-md:hidden" />
        </colgroup>
        <thead>
          <tr className="text-xs text-muted-foreground">
            <th scope="col" className="py-2 pl-3 font-medium">
              #
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Opportunity, in the user's words
            </th>
            <th scope="col" className="py-2 pr-3 font-medium max-md:hidden">
              Evidence
            </th>
            <th scope="col" className="py-2 pr-3 font-medium max-lg:hidden">
              Sources
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Score
            </th>
            <th scope="col" className="py-2 pr-3 font-medium max-md:hidden">
              Status
            </th>
            <th scope="col" className="py-2 pr-2 max-md:hidden">
              <span className="sr-only">Triage</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {sections.map((section) => (
            <Fragment key={section.band}>
              <tr
                data-testid={`opportunity-band-${section.band}`}
                className="border-t border-border"
              >
                <th
                  scope="colgroup"
                  colSpan={7}
                  className="bg-muted/40 px-3 py-1.5 text-xs font-semibold tracking-wide uppercase"
                >
                  {section.label}{" "}
                  {section.note && (
                    <span className="font-normal tracking-normal text-muted-foreground normal-case">
                      {section.note}
                    </span>
                  )}
                </th>
              </tr>
              {section.rows.map((row) => {
                const i = index++;
                return (
                  <Row
                    key={row.id}
                    row={row}
                    index={i}
                    selected={row.id === selectedId}
                    nowS={nowS}
                    canAct={canAct}
                    handlers={handlers}
                    onSelect={onSelect}
                  />
                );
              })}
            </Fragment>
          ))}
        </tbody>
      </table>
      {footer && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-3 py-2 text-xs text-muted-foreground">
          {footer}
        </div>
      )}
    </section>
  );
}
