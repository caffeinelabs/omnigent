/**
 * Steer › Strategy and goals: what the factory is trying to achieve. Goal
 * cards (statement, metric with "not measured yet", baseline, target, date,
 * weight); focus segments as toggle chips (toggling one stages
 * `strategy.focus_segments`, and the change column shows how opportunities
 * re-rank); non-goals and guardrails as chips; where capacity goes, target
 * against now, as four sliders that must add up to 100. Strategy is judged on
 * outcomes, so it applies as a version and never runs as a trial.
 */

import { CheckIcon, PlusIcon, TargetIcon, Trash2Icon, XIcon } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { GoalDef, ProposalView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { steerPageHref } from "../factoryRoutes";
import { EmptyState, SectionCard } from "../project/projectParts";
import { DATA_HUE, FOCUS_RING, MEANING } from "../ui/statusTokens";
import { useFactoryToast } from "../ui/Toast";
import { useSteer } from "./SteerContext";
import {
  ChipListField,
  NumberField,
  STAGED_CONTROL,
  SliderField,
  StagedChip,
  TextField,
} from "./steerFields";
import {
  MIX_KEYS,
  MIX_LABEL,
  type MixKey,
  agentProposals,
  goalMeta,
  mixNow,
  mixTotal,
  proposalPaths,
  proposerName,
  stageProposal,
  stagedList,
  under,
} from "./steerModel";

const SHADE = [1, 0.7, 0.45, 0.25];

function MixBar({
  mix,
  total,
  label,
}: {
  mix: Record<MixKey, number>;
  total: number;
  label: string;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
      <span className="w-14 shrink-0">{label}</span>
      <div
        role="img"
        aria-label={`${label}: ${MIX_KEYS.map((k) => `${MIX_LABEL[k]} ${total ? Math.round((mix[k] / total) * 100) : 0} %`).join(", ")}`}
        className="flex h-2.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted"
      >
        {MIX_KEYS.map((k, i) => (
          <i
            key={k}
            style={{
              width: `${total ? (mix[k] / total) * 100 : 0}%`,
              background: DATA_HUE,
              opacity: SHADE[i],
            }}
            className="block h-full border-r border-card last:border-0"
          />
        ))}
      </div>
    </div>
  );
}

function parseDate(text: string): { value: unknown } | { error: string } {
  const t = text.trim();
  if (!t) return { value: null };
  return /^\d{4}-\d{2}-\d{2}$/.test(t) ? { value: t } : { error: "A date as YYYY-MM-DD" };
}

function GoalCard({ goal }: { goal: GoalDef & { staged?: "added" | "changed" | "removed" } }) {
  const steer = useSteer();
  const base = `strategy.goals.${goal.id}`;
  if (goal.staged === "removed") {
    return (
      <div className={cn("flex items-center gap-2 rounded-lg border p-3 text-sm", STAGED_CONTROL)}>
        <s className="min-w-0 flex-1 text-muted-foreground">{goal.statement}</s>
        <StagedChip />
      </div>
    );
  }
  return (
    <div
      data-testid={`steer-goal-${goal.id}`}
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-3"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="rounded-md border border-border px-1.5 font-mono text-xs">{goal.id}</span>
        <span className="min-w-0 flex-1 text-xs text-muted-foreground">{goalMeta(goal)}</span>
        {steer.canEdit && !steer.frozen && (
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label={`Remove goal ${goal.id}`}
            onClick={() =>
              steer.removeItem("strategy", "strategy.goals", goal.id, `Goal ${goal.id}`)
            }
            componentId="factory.steer.goal_remove"
          >
            <Trash2Icon />
          </Button>
        )}
      </div>
      <TextField section="strategy" path={`${base}.statement`} label="Statement" maxLength={280} />
      <div className="grid min-w-0 grid-cols-1 gap-x-4 sm:grid-cols-2">
        <TextField
          section="strategy"
          path={`${base}.metric`}
          label="Metric"
          placeholder="not measured yet"
          help={
            goal.metric ? undefined : (
              <span>
                <span className="rounded bg-muted px-1 font-medium text-foreground">
                  not measured yet
                </span>{" "}
                No source can measure it yet; opportunities still rank against the goal.
              </span>
            )
          }
          maxLength={120}
        />
        <TextField section="strategy" path={`${base}.by`} label="By (date)" parse={parseDate} />
      </div>
      <div className="flex min-w-0 flex-col divide-y divide-border/60">
        <NumberField section="strategy" path={`${base}.baseline`} label="Baseline" />
        <NumberField section="strategy" path={`${base}.target`} label="Target" />
        <NumberField
          section="strategy"
          path={`${base}.weight`}
          label="Weight"
          help="against the other goals"
          bounds={[0, 10]}
        />
      </div>
    </div>
  );
}

function AddedGoal({ goal, editKey }: { goal: Partial<GoalDef>; editKey: string }) {
  const steer = useSteer();
  return (
    <div
      className={cn("flex min-w-0 items-start gap-2 rounded-lg border p-3 text-sm", STAGED_CONTROL)}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex items-center gap-1.5">
          <span className="font-medium">New goal</span>
          <StagedChip />
        </span>
        <span className="break-words">{goal.statement}</span>
        <span className="text-xs text-muted-foreground">
          {[
            goal.metric ?? "not measured yet",
            goal.target != null ? `target ${goal.target}` : null,
            goal.by ? `by ${goal.by}` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </div>
      {steer.canEdit && (
        <button
          type="button"
          aria-label="Drop the new goal"
          onClick={() => steer.drop(editKey)}
          className={cn("cursor-pointer rounded p-0.5 hover:bg-muted", FOCUS_RING)}
        >
          <XIcon aria-hidden className="size-3.5" />
        </button>
      )}
    </div>
  );
}

function AddGoalForm({ onDone }: { onDone: () => void }) {
  const steer = useSteer();
  const id = useId();
  const [statement, setStatement] = useState("");
  const [metric, setMetric] = useState("");
  const [target, setTarget] = useState("");
  const [by, setBy] = useState("");
  const valid =
    statement.trim().length > 0 &&
    (target.trim() === "" || Number.isFinite(Number(target))) &&
    (by.trim() === "" || /^\d{4}-\d{2}-\d{2}$/.test(by.trim()));
  const submit = () => {
    if (!valid) return;
    steer.addItem(
      "strategy",
      "strategy.goals",
      "New goal",
      {
        statement: statement.trim(),
        metric: metric.trim() || null,
        target: target.trim() ? Number(target) : null,
        by: by.trim() || null,
        weight: 1,
      },
      "Opportunities rank against it; the server numbers it G-n.",
    );
    onDone();
  };
  return (
    <form
      data-testid="steer-add-goal"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="grid min-w-0 grid-cols-1 gap-2 rounded-lg border border-dashed border-border p-3 sm:grid-cols-2"
    >
      <label className="flex flex-col gap-1 text-sm sm:col-span-2" htmlFor={`${id}-s`}>
        Statement
        <Input
          id={`${id}-s`}
          value={statement}
          maxLength={280}
          onChange={(e) => setStatement(e.target.value)}
          componentId="factory.steer.goal_statement"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm" htmlFor={`${id}-m`}>
        Metric (leave empty while nothing measures it)
        <Input
          id={`${id}-m`}
          value={metric}
          maxLength={120}
          onChange={(e) => setMetric(e.target.value)}
          componentId="factory.steer.goal_metric"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm" htmlFor={`${id}-t`}>
        Target
        <Input
          id={`${id}-t`}
          value={target}
          inputMode="decimal"
          onChange={(e) => setTarget(e.target.value)}
          componentId="factory.steer.goal_target"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm" htmlFor={`${id}-b`}>
        By (YYYY-MM-DD)
        <Input
          id={`${id}-b`}
          value={by}
          onChange={(e) => setBy(e.target.value)}
          componentId="factory.steer.goal_by"
        />
      </label>
      <span className="flex items-end justify-end gap-2">
        <Button
          size="sm"
          variant="ghost"
          type="button"
          onClick={onDone}
          componentId="factory.steer.goal_cancel"
        >
          Cancel
        </Button>
        <Button size="sm" type="submit" disabled={!valid} componentId="factory.steer.goal_stage">
          Stage goal
        </Button>
      </span>
    </form>
  );
}

function goalSuggestion(proposals: readonly ProposalView[]): ProposalView | null {
  return (
    agentProposals(proposals).find((p) =>
      proposalPaths(p).some((path) => under(path, "strategy.goals")),
    ) ?? null
  );
}

export function Strategy({ proposals }: { proposals: readonly ProposalView[] | undefined }) {
  const steer = useSteer();
  const toast = useFactoryToast();
  const [adding, setAdding] = useState(false);
  const { effective, staging, factoryId } = steer;
  const goals = stagedList(staging, "strategy.goals", effective.strategy?.goals ?? []);
  const segments = stagedList(
    staging,
    "product.segments",
    effective.product?.segments ?? [],
  ).filter((s) => s.staged !== "removed");
  const focus = (steer.value("strategy.focus_segments") as string[] | null) ?? [];
  const focusNow = effective.strategy?.focus_segments ?? [];
  const offFocus = steer.value("discovery.rubric.off_focus_factor");
  const mix = Object.fromEntries(
    MIX_KEYS.map((k) => [k, Number(steer.value(`strategy.mix.${k}`)) || 0]),
  ) as Record<MixKey, number>;
  const total = mixTotal(mix);
  const active = steer.snapshot.projects.filter(
    (p) => p.overlay !== "failed" && !["merged", "done"].includes(p.stage),
  );
  const now = mixNow(active);
  const nowTotal = active.length;
  const gaps = MIX_KEYS.map((k) => ({
    k,
    gap: mix[k] - (nowTotal ? Math.round((now[k] / nowTotal) * 100) : 0),
  })).sort((a, b) => b.gap - a.gap);
  const biggest = gaps[0];
  const suggestion = goalSuggestion(proposals ?? []);
  const toggle = (id: string) => {
    const next = focus.includes(id) ? focus.filter((f) => f !== id) : [...focus, id];
    steer.replace(
      "strategy",
      "strategy.focus_segments",
      "Focus segments",
      next,
      "Changes fit for every opportunity in these segments. Coverage gaps are reported for them.",
    );
  };
  const stageSuggestion = (p: ProposalView) => {
    const before = staging;
    steer.setStaging(stageProposal(p, effective));
    toast.show({
      message: `Staged ${proposerName(p)}'s suggestion (${p.ref}).`,
      undo: () => steer.setStaging(before),
    });
  };
  const addedGoals = staging.edits.filter(
    (e) => e.change.op === "add" && e.change.path === "strategy.goals",
  );
  return (
    <div data-testid="steer-strategy" className="flex min-w-0 flex-col gap-4">
      <SectionCard
        title="Goals"
        testId="steer-goals"
        action={
          steer.canEdit && !steer.frozen && !adding ? (
            <Button
              size="xs"
              variant="outline"
              onClick={() => setAdding(true)}
              data-testid="steer-add-goal-open"
              componentId="factory.steer.goal_add"
            >
              <PlusIcon />
              Add goal
            </Button>
          ) : undefined
        }
      >
        {goals.filter((g) => g.staged !== "added").length === 0 &&
        addedGoals.length === 0 &&
        !adding ? (
          <EmptyState
            testId="steer-goals-empty"
            icon={<TargetIcon aria-hidden className="size-6 text-muted-foreground/50" />}
            title="No goal yet"
            body={
              suggestion
                ? `Opportunities stay unassigned until one exists. ${proposerName(suggestion)} suggested one: ${suggestion.title}`
                : "Opportunities stay unassigned until one exists. A goal says what the factory is trying to achieve, how it is measured once a source can, and by when."
            }
            action={
              suggestion && steer.canEdit ? (
                <Button
                  size="sm"
                  onClick={() => stageSuggestion(suggestion)}
                  componentId="factory.steer.goal_suggestion"
                >
                  Stage {proposerName(suggestion)}'s suggestion
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className="flex min-w-0 flex-col gap-2.5">
            {goals
              .filter((g) => g.staged !== "added")
              .map((g) => (
                <GoalCard key={g.id} goal={g} />
              ))}
            {addedGoals.map((e) => (
              <AddedGoal key={e.key} goal={e.after as Partial<GoalDef>} editKey={e.key} />
            ))}
          </div>
        )}
        {adding && <AddGoalForm onDone={() => setAdding(false)} />}
      </SectionCard>
      <SectionCard title="Focus segments" testId="steer-focus">
        <p className="text-sm text-muted-foreground">
          Opportunities outside these segments get fit ×{" "}
          {typeof offFocus === "number" ? offFocus : 0.5}. Coverage gaps are reported for them.
        </p>
        {segments.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No segments yet.{" "}
            <Link
              to={steerPageHref(factoryId, "product")}
              componentId="factory.steer.to_product"
              className="text-status-blue-fg hover:underline"
            >
              Define segments in the product profile
            </Link>{" "}
            first.
          </p>
        ) : (
          <div role="group" aria-label="Focus segments" className="flex flex-wrap gap-1.5">
            {segments.map((s) => {
              const on = focus.includes(s.id);
              const changed = on !== focusNow.includes(s.id);
              return (
                <button
                  key={s.id}
                  type="button"
                  aria-pressed={on}
                  disabled={!steer.canEdit || steer.frozen != null}
                  onClick={() => toggle(s.id)}
                  data-testid={`steer-focus-${s.id}`}
                  data-staged={changed ? "true" : undefined}
                  className={cn(
                    "inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-full border px-3 text-sm disabled:cursor-default",
                    on
                      ? cn("border-status-blue", MEANING.selected.text)
                      : "border-border text-muted-foreground",
                    changed && STAGED_CONTROL,
                    FOCUS_RING,
                  )}
                >
                  {on && <CheckIcon aria-hidden className="size-3.5" />}
                  {s.label}
                </button>
              );
            })}
          </div>
        )}
      </SectionCard>
      <SectionCard title="Non-goals" testId="steer-non-goals">
        <ChipListField
          section="strategy"
          path="strategy.non_goals"
          label="Non-goals"
          help="An opportunity under a non-goal is parked, with the reason shown. It never reaches Atlas."
          placeholder="Add a non-goal"
        />
      </SectionCard>
      <SectionCard title="Guardrails" testId="steer-guardrails">
        <ChipListField
          section="strategy"
          path="strategy.guardrails"
          label="Guardrails"
          help="What must not get worse while the factory chases the goals. Every bet carries them."
          placeholder="Add a guardrail"
        />
      </SectionCard>
      <SectionCard
        title="Where capacity goes"
        testId="steer-mix"
        meta={
          <span className={total === 100 ? undefined : "text-destructive"}>
            total {total} %{total === 100 ? "" : " (must be 100)"}
          </span>
        }
      >
        <div className="flex min-w-0 flex-col gap-1.5">
          <MixBar mix={mix} total={total || 1} label="Target" />
          <MixBar mix={now} total={nowTotal || 1} label="Now" />
        </div>
        <div className="flex min-w-0 flex-col">
          {MIX_KEYS.map((k) => (
            <SliderField
              key={k}
              section="strategy"
              path={`strategy.mix.${k}`}
              label={MIX_LABEL[k]}
              help={`now ${nowTotal ? Math.round((now[k] / nowTotal) * 100) : 0} % of ${nowTotal} active ${nowTotal === 1 ? "project" : "projects"}`}
              min={0}
              max={100}
              step={5}
              format={(v) => `${v} %`}
            />
          ))}
        </div>
        {biggest && biggest.gap > 0 && (
          <p className="text-sm">
            Biggest gap: <b className="font-semibold">{MIX_LABEL[biggest.k].toLowerCase()}</b>,{" "}
            {biggest.gap} points under target. Atlas fills it first when it plans. Requested work
            without evidence counts as hunches.
          </p>
        )}
      </SectionCard>
      <p className="text-sm text-muted-foreground">
        Strategy changes apply as a new definition version with a re-rank preview. They are not
        trialed: trials judge delivery metrics, and strategy is judged on outcomes.
      </p>
    </div>
  );
}
