/**
 * The Health panel (`?peek=health`), opened from the verdict pill: why the
 * dot has its colour (each fact links to where it is fixed), spend against
 * the alert, the machine facts the classic header showed in a row (live
 * updates, state, engine mode, definition version, base and env, cost,
 * running time, who is here), how the verdict is built, and the factory-wide
 * levers (Pause all, the ⋯ controls, Incidents and findings).
 */

import { ChevronRightIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { FactorySnapshot } from "@/lib/factoryApi";
import type { StatusSpend, StatusVerdict } from "@/lib/factoryStatusApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { factoryElapsed } from "../board/boardModel";
import { LiveDot } from "../board/FactoryHeader";
import { FactoryControlsMenu } from "../controls/FactoryControlsMenu";
import { FactoryStateChip } from "../FactoryIndexCard";
import { debugPageHref } from "../factoryRoutes";
import { formatElapsed, formatUsd } from "../factoryFormat";
import { Peek } from "../ui/Peek";
import { DATA_HUE, FOCUS_RING, MEANING } from "../ui/statusTokens";
import { BaseEnvLine } from "./BaseEnvLine";
import { FactoryPresence } from "./FactoryPresence";
import { VerdictDotMark, verdictRate } from "./FactoryTopBar";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex min-w-0 flex-col gap-2 border-b border-border pb-4 last:border-0">
      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[8.5rem_minmax(0,1fr)] items-baseline gap-2 text-sm max-[520px]:grid-cols-1 max-[520px]:gap-0.5">
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

/** Spend against a limit as a thin bar (amber past the limit: someone should look). */
function SpendBar({ value, limit, label }: { value: number; limit: number; label: string }) {
  const share = Math.max(0, Math.min(1, value / limit));
  const over = value > limit;
  return (
    <div
      role="img"
      aria-label={label}
      className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
      data-testid="health-rate-bar"
    >
      <div
        className="h-full rounded-full"
        style={{
          width: `${Math.round(share * 100)}%`,
          background: over ? MEANING.needsYou.hue : DATA_HUE,
        }}
      />
    </div>
  );
}

export function HealthPeek({
  open,
  onOpenChange,
  snapshot,
  nowS,
  verdict,
  spend,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  snapshot: FactorySnapshot;
  nowS: number;
  verdict: StatusVerdict;
  /** The status summary's spend (absent while it loads). */
  spend: StatusSpend | null;
}) {
  const { factory } = snapshot;
  const fid = factory.id;
  const rate = verdict.usd_per_h ?? snapshot.totals.usd_per_h ?? null;
  const alert = verdict.usd_per_h_alert ?? null;
  const elapsed = formatElapsed(factoryElapsed(factory, nowS));
  return (
    <Peek
      open={open}
      onOpenChange={onOpenChange}
      title={
        <span className="inline-flex items-center gap-2" data-testid="health-title">
          <VerdictDotMark dot={verdict.dot} />
          {verdict.sentence}
        </span>
      }
      description={verdictRate(verdict) ?? undefined}
      footer={
        <>
          <Link
            to={debugPageHref(fid, "incidents")}
            componentId="factory.health.incidents"
            data-testid="health-incidents"
            onClick={() => onOpenChange(false)}
            className={cn(
              "inline-flex h-8 items-center gap-1 rounded-md border border-border px-3 text-sm hover:bg-muted",
              FOCUS_RING,
            )}
          >
            Incidents and findings
            <ChevronRightIcon aria-hidden className="size-3.5" />
          </Link>
          <span className="ml-auto">
            <FactoryControlsMenu factoryId={fid} factory={factory} members={snapshot.members} />
          </span>
        </>
      }
    >
      <div data-testid="health-peek" className="flex min-w-0 flex-col gap-4">
        <Section title="Why">
          {verdict.reasons.length ? (
            <ul className="flex flex-col gap-1.5" data-testid="health-reasons">
              {verdict.reasons.map((reason) => (
                <li
                  key={`${reason.kind}:${reason.text}`}
                  className="flex items-start gap-2 text-sm"
                >
                  <span
                    aria-hidden
                    className="mt-1.5 size-1.5 shrink-0 rounded-full"
                    style={{
                      background:
                        reason.kind === "waiting"
                          ? MEANING.needsYou.hue
                          : reason.kind === "blocked"
                            ? MEANING.stuck.hue
                            : MEANING.broken.hue,
                    }}
                  />
                  <Link
                    to={reason.href}
                    componentId={`factory.health.reason.${reason.kind}`}
                    onClick={() => onOpenChange(false)}
                    className="min-w-0 underline-offset-2 hover:underline"
                  >
                    {reason.text}
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="health-no-reasons">
              Nothing is broken and nothing waits on you.
            </p>
          )}
        </Section>
        <Section title="Spend">
          <dl className="flex flex-col gap-1.5">
            {spend?.last_24h_usd != null && (
              <Fact term="Last 24 h">{formatUsd(spend.last_24h_usd)}</Fact>
            )}
            <Fact term="Per hour">
              {rate == null ? (
                <span className="text-muted-foreground">not measured yet</span>
              ) : (
                <span className="flex flex-col gap-1">
                  <span className="font-mono tabular-nums">
                    {formatUsd(rate)}/h
                    {alert != null && (
                      <span className="text-muted-foreground">
                        {" "}
                        · alert above {formatUsd(alert)}/h
                      </span>
                    )}
                  </span>
                  {alert != null && (
                    <SpendBar
                      value={rate}
                      limit={alert}
                      label={`${formatUsd(rate)} per hour of a ${formatUsd(alert)} per hour alert`}
                    />
                  )}
                </span>
              )}
            </Fact>
            {factory.cost_usd != null && (
              <Fact term="So far">
                <span className="font-mono tabular-nums">{formatUsd(factory.cost_usd)}</span>
              </Fact>
            )}
          </dl>
        </Section>
        <Section title="Machine">
          <dl className="flex flex-col gap-1.5" data-testid="health-facts">
            <Fact term="Updates">
              <LiveDot />
            </Fact>
            <Fact term="State">
              <FactoryStateChip state={factory.state} />
            </Fact>
            {snapshot.engine_mode && (
              <Fact term="Engine">
                <span className="font-mono">{snapshot.engine_mode}</span>
              </Fact>
            )}
            {snapshot.def_version != null && (
              <Fact term="Definition">
                <span className="font-mono">v{snapshot.def_version}</span>
              </Fact>
            )}
            {(snapshot.health.config ?? []).length > 0 && (
              <Fact term="Config">
                <span className="text-destructive" data-testid="health-fact-config">
                  {snapshot.health.config!.join("; ")}
                </span>
              </Fact>
            )}
            {factory.base && (
              <Fact term="Base">
                <BaseEnvLine base={factory.base} nowS={nowS} />
              </Fact>
            )}
            {elapsed && (
              <Fact term="Running for">
                <span className="font-mono tabular-nums">{elapsed}</span>
              </Fact>
            )}
            <Fact term="Here now">
              <FactoryPresence
                factoryId={fid}
                presence={snapshot.presence ?? []}
                selfId={snapshot.viewer?.user_id}
              />
            </Fact>
          </dl>
        </Section>
        <Section title="How the verdict is built">
          <p className="text-sm text-muted-foreground">
            Red when something is broken: a project or a lead stopped, a critical incident, GitHub
            out of reach or the supervisor failing. Amber when nothing is broken and things wait on
            you. Grey when the factory is paused, finished or idle. Green otherwise. A blocked
            project is named in the sentence without changing the colour. It is built from facts by
            fixed rules, never written by an agent, and your Slack digest opens with the same
            sentence.
          </p>
        </Section>
      </div>
    </Peek>
  );
}
