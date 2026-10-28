/**
 * The Timeline (`/factory/:factoryId/timeline`): swimlanes per agent, loop,
 * PR and env, grouped by project, from the spans API. A window (1h, 6h,
 * 24h, All), what to show (turns, tools, waits, loops, gates; heartbeats
 * hidden by default), a project or agents filter (`?focus=`,
 * `?agents=`), and a replay scrubber that redraws it "as of" a moment
 * (`?at=`). Any bar opens its agent at that time, its PR or its project.
 */

import { useEffect, useMemo, useState } from "react";
import { GanttChartIcon, PauseIcon, PlayIcon, SkipBackIcon, SkipForwardIcon } from "lucide-react";
import { isMissingRoute } from "@/components/factory/agents/opsQueries";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { clock } from "@/components/factory/overview/LiveFeed";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import {
  ALL_MS,
  DEFAULT_SHOW,
  type TimelineShow,
  type TimelineWindow,
  WINDOW_MS,
  buildTimeline,
  windowStart,
} from "@/components/factory/timeline/timelineModel";
import { TimelineLegend, TimelineView } from "@/components/factory/timeline/TimelineView";
import { Button } from "@/components/ui/button";
import { useFactorySpans } from "@/hooks/useFactory";
import { useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

const WINDOWS: TimelineWindow[] = ["1h", "6h", "24h", "all"];
const SHOW_KEYS: (keyof TimelineShow)[] = [
  "turns",
  "tools",
  "waits",
  "loops",
  "gates",
  "heartbeats",
];
/** Replay steps a minute every half second while playing. */
const PLAY_STEP_MS = 60_000;
const PLAY_TICK_MS = 500;
const FETCH_GRAIN_MS = 10 * 60_000;

function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  testId,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
  testId: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      data-testid={testId}
      className="inline-flex rounded-lg bg-muted p-[3px]"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          onClick={() => onChange(option.value)}
          data-testid={`${testId}-${option.value}`}
          className={cn(
            "h-6 cursor-pointer rounded-md px-2 text-sm font-medium focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
            value === option.value
              ? "bg-background text-foreground shadow-sm dark:bg-input/30"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function TimelinePage() {
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const [params, setParams] = useSearchParams();
  const win = (WINDOWS as string[]).includes(params.get("window") ?? "")
    ? (params.get("window") as TimelineWindow)
    : "1h";
  const atParam = Number(params.get("at"));
  const nowMs = Math.floor(nowS / 10) * 10_000;
  const replayAt = Number.isFinite(atParam) && atParam > 0 && atParam < nowMs ? atParam : null;
  const project = params.get("focus");
  const agents = useMemo(() => (params.get("agents") ?? "").split(",").filter(Boolean), [params]);
  const grouped = params.get("group") !== "none";
  const [show, setShow] = useState<TimelineShow>(DEFAULT_SHOW);
  const [playing, setPlaying] = useState(false);
  const set = (patch: Record<string, string | null>) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, value] of Object.entries(patch)) {
          if (value == null || value === "") next.delete(key);
          else next.set(key, value);
        }
        return next;
      },
      { replace: true },
    );
  const to = replayAt ?? nowMs;
  const span = win === "all" ? ALL_MS : WINDOW_MS[win];
  const since = Math.floor((to - span) / FETCH_GRAIN_MS) * FETCH_GRAIN_MS;
  const spans = useFactorySpans(fid, since);
  const data = spans.data?.data;
  const from = useMemo(() => windowStart(win, to, data ?? []), [win, to, data]);
  const groups = useMemo(() => {
    const built = buildTimeline(data ?? [], snapshot, {
      from,
      to,
      show,
      replay: replayAt != null,
      agents: agents.length ? agents : null,
      project,
      nowS,
    });
    return grouped
      ? built
      : built.length
        ? [{ key: "all", label: "All lanes", project: null, lanes: built.flatMap((g) => g.lanes) }]
        : [];
  }, [data, snapshot, from, to, show, replayAt, agents, project, nowS, grouped]);
  const scrubFrom = nowMs - (win === "all" ? ALL_MS : Math.max(span, 6 * 3_600_000));

  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      const next = (replayAt ?? scrubFrom) + PLAY_STEP_MS;
      if (next >= nowMs) {
        setPlaying(false);
        set({ at: null });
      } else {
        set({ at: String(next) });
      }
    }, PLAY_TICK_MS);
    return () => clearInterval(timer);
    // `set` writes the URL; the effect restarts on each step's new `at`.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, replayAt, nowMs, scrubFrom]);

  const lanes = groups.reduce((n, g) => n + g.lanes.length, 0);
  return (
    <div data-testid="factory-timeline-page" className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-base font-semibold">Timeline</h1>
        <Button
          size="xs"
          variant={replayAt == null ? "secondary" : "outline"}
          aria-pressed={replayAt == null}
          onClick={() => {
            setPlaying(false);
            set({ at: null });
          }}
          data-testid="timeline-now"
          componentId="factory.timeline.now"
        >
          <span
            aria-hidden
            className={replayAt == null ? "text-session-active" : "text-muted-foreground"}
          >
            ●
          </span>
          Now
        </Button>
        <Segmented
          value={win}
          label="Window"
          testId="timeline-window"
          options={WINDOWS.map((w) => ({ value: w, label: w === "all" ? "All" : w }))}
          onChange={(w) => set({ window: w === "1h" ? null : w })}
        />
        <Segmented
          value={grouped ? "project" : "none"}
          label="Group"
          testId="timeline-group"
          options={[
            { value: "project", label: "By project" },
            { value: "none", label: "Flat" },
          ]}
          onChange={(g) => set({ group: g === "project" ? null : g })}
        />
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 text-sm">
        <fieldset
          data-testid="timeline-show"
          className="flex flex-wrap items-center gap-x-3 gap-y-1"
        >
          <legend className="sr-only">Show</legend>
          <span className="text-muted-foreground">Show</span>
          {SHOW_KEYS.map((key) => (
            <label key={key} className="inline-flex cursor-pointer items-center gap-1.5">
              <input
                type="checkbox"
                checked={show[key]}
                data-testid={`timeline-show-${key}`}
                onChange={(event) => setShow((prev) => ({ ...prev, [key]: event.target.checked }))}
                className="size-3.5 cursor-pointer rounded-sm accent-primary"
              />
              {key}
            </label>
          ))}
        </fieldset>
        <div
          data-testid="timeline-replay"
          className="flex min-w-0 flex-1 items-center gap-1.5 md:justify-end"
        >
          <span className="text-muted-foreground">Replay</span>
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label="Back 5 minutes"
            onClick={() => set({ at: String(Math.max(scrubFrom, to - 5 * 60_000)) })}
            data-testid="timeline-replay-back"
            componentId="factory.timeline.replay_back"
          >
            <SkipBackIcon />
          </Button>
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label={playing ? "Pause replay" : "Play replay"}
            onClick={() => setPlaying((p) => !p)}
            data-testid="timeline-replay-play"
            componentId="factory.timeline.replay_play"
          >
            {playing ? <PauseIcon /> : <PlayIcon />}
          </Button>
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label="Forward 5 minutes"
            disabled={replayAt == null}
            onClick={() => {
              const next = to + 5 * 60_000;
              set({ at: next >= nowMs ? null : String(next) });
            }}
            data-testid="timeline-replay-forward"
            componentId="factory.timeline.replay_forward"
          >
            <SkipForwardIcon />
          </Button>
          <input
            type="range"
            min={scrubFrom}
            max={nowMs}
            step={60_000}
            value={to}
            aria-label="Replay as of"
            data-testid="timeline-scrubber"
            onChange={(event) => {
              const value = Number(event.target.value);
              set({ at: value >= nowMs - 60_000 ? null : String(value) });
            }}
            className="h-4 min-w-24 flex-1 cursor-pointer accent-session-active md:max-w-56"
          />
          <span className="shrink-0 font-mono text-xs tabular-nums">{clock(to).slice(0, 5)}</span>
        </div>
      </div>
      {(replayAt != null || agents.length > 0 || project) && (
        <p
          data-testid="timeline-scope"
          className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground"
        >
          {replayAt != null && (
            <span>
              Replaying as of <span className="font-mono text-foreground">{clock(replayAt)}</span>
            </span>
          )}
          {agents.length > 0 && (
            <span>
              {agents.length === 1 ? "1 agent" : `${agents.length} agents`} ·{" "}
              <button
                type="button"
                onClick={() => set({ agents: null })}
                className="cursor-pointer underline underline-offset-2 hover:text-foreground"
              >
                show all
              </button>
            </span>
          )}
          {project && (
            <span>
              <span className="font-mono">{project}</span> ·{" "}
              <button
                type="button"
                onClick={() => set({ focus: null })}
                className="cursor-pointer underline underline-offset-2 hover:text-foreground"
              >
                every project
              </button>
            </span>
          )}
        </p>
      )}
      {spans.isLoading && !data ? (
        <div
          aria-busy="true"
          aria-label="Loading the timeline"
          data-testid="timeline-loading"
          className="flex flex-col gap-1 rounded-xl border border-border p-3"
        >
          {Array.from({ length: 9 }, (_, i) => (
            <div
              key={i}
              className="h-5 animate-pulse rounded bg-muted/70"
              style={{ width: `${55 + ((i * 17) % 40)}%` }}
            />
          ))}
        </div>
      ) : spans.error && !data && !isMissingRoute(spans.error) ? (
        <FactoryLoadError
          error={spans.error}
          updatedAt={0}
          onRetry={() => void spans.refetch()}
          what="the timeline"
          testId="timeline-error"
        />
      ) : lanes === 0 ? (
        <EmptyState
          testId="timeline-empty"
          icon={<GanttChartIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title={
            spans.error ? "This server records no spans yet" : "Nothing recorded in this window"
          }
          body="Agents’ work, waits, loop attempts and PR milestones show here as lanes. Try a longer window."
        />
      ) : (
        <TimelineView factoryId={fid} groups={groups} from={from} to={to} replayAt={replayAt} />
      )}
      <TimelineLegend />
    </div>
  );
}
