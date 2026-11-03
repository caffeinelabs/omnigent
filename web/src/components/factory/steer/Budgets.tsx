/**
 * Steer › Budgets and autonomy: money (per project, per day, the spend
 * alert), the loops' attempts and rounds, discovery's budget while it runs,
 * and who decides what (the autonomy level in plain words, project
 * proposals, bets from opportunities, the engine's mode). Lowering a money
 * budget only reduces spend, so owners and editors apply it at once (spec
 * §8.4); raising one, and the loops and autonomy, are owner changes that
 * editors propose. The change column's preview says which. Every field stages.
 */

import type { AutonomyLevelText } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { SectionCard } from "../project/projectParts";
import { AUTONOMY_GROUPS, BUDGET_GROUPS, getPath } from "../settings/settingsModel";
import { MEANING } from "../ui/statusTokens";
import { useSteer } from "./SteerContext";
import { FieldGroups, NumberField, SelectField } from "./steerFields";

/** The lines every level shares, and what each level adds to them. */
export function levelDiff(levels: readonly AutonomyLevelText[]): {
  atOnce: string[];
  waits: string[];
  adds: Record<string, { atOnce: string[]; waits: string[] }>;
} {
  const shared = (pick: (l: AutonomyLevelText) => readonly string[]) =>
    (levels[0] ? pick(levels[0]) : []).filter((line) =>
      levels.every((l) => pick(l).includes(line)),
    );
  const atOnce = shared((l) => l.at_once);
  const waits = shared((l) => l.waits);
  const adds = Object.fromEntries(
    levels.map((l) => [
      l.level,
      {
        atOnce: l.at_once.filter((line) => !atOnce.includes(line)),
        waits: l.waits.filter((line) => !waits.includes(line)),
      },
    ]),
  );
  return { atOnce, waits, adds };
}

function Lines({ title, lines }: { title: string; lines: readonly string[] }) {
  if (lines.length === 0) return null;
  return (
    <>
      <p className="mt-1 text-xs font-semibold text-foreground">{title}</p>
      <ul className="m-0 flex list-disc flex-col gap-0.5 pl-4 text-muted-foreground">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </>
  );
}

/**
 * What each level does, in plain words (the server's `autonomy_levels`): what
 * each level lets through beyond the others, then what every level shares.
 */
export function AutonomyLevels({
  levels,
  current,
}: {
  levels: readonly AutonomyLevelText[];
  current: string;
}) {
  if (levels.length === 0) return null;
  const diff = levelDiff(levels);
  return (
    <SectionCard
      title="What each autonomy level does"
      meta="owners set it; agents never change it"
      testId="steer-autonomy-levels"
    >
      <ul className="m-0 grid list-none gap-3 p-0 md:grid-cols-3">
        {levels.map((level) => {
          const active = level.level === current;
          const adds = diff.adds[level.level] ?? { atOnce: [], waits: [] };
          return (
            <li
              key={level.level}
              data-testid={`steer-autonomy-level-${level.level}`}
              data-current={active || undefined}
              className={cn(
                "flex min-w-0 flex-col gap-1.5 rounded-lg border p-3 text-sm",
                active ? MEANING.selected.border : "border-border",
              )}
            >
              <p className="font-semibold text-foreground">
                {level.label}
                {active && <span className="ml-1.5 text-xs font-normal">(in force)</span>}
              </p>
              <p className="text-muted-foreground">{level.summary}</p>
              <Lines title="Also applies at once" lines={adds.atOnce} />
              <Lines title="Still waits for a person" lines={adds.waits} />
            </li>
          );
        })}
      </ul>
      <div
        data-testid="steer-autonomy-every-level"
        className="mt-3 grid gap-3 text-sm md:grid-cols-2"
      >
        <div>
          <Lines title="At every level, applies at once" lines={diff.atOnce} />
        </div>
        <div>
          <Lines title="At every level, waits for a person" lines={diff.waits} />
        </div>
      </div>
    </SectionCard>
  );
}

const BETS = [
  { value: "confirm", label: "Confirm: every bet waits for a person" },
  { value: "requested", label: "Requested: bets a person asked for apply with Undo" },
  { value: "grace", label: "Grace: bets apply after a grace window unless rejected" },
];

export function Budgets() {
  const steer = useSteer();
  const bounds = steer.definition.bounds;
  return (
    <div data-testid="steer-budgets" className="flex min-w-0 flex-col gap-4">
      <p data-testid="steer-budgets-who" className="text-sm text-muted-foreground">
        Lowering a dollar budget applies at once, for editors too. Raising one, and other loop or
        autonomy changes, wait for an owner: editors propose them. The preview says which, and every
        change is one version you can undo.
      </p>
      <FieldGroups section="budgets" groups={BUDGET_GROUPS} testId="steer-budget-fields" />
      {steer.discoveryOn && (
        <SectionCard title="Discovery" meta="Scout stops at 100 %" testId="steer-discovery-budget">
          <div className="flex min-w-0 flex-col divide-y divide-border/60">
            <NumberField
              section="budgets"
              path="discovery.budgets.usd_per_day"
              label="Discovery per day"
              unit="$/day"
              bounds={bounds["discovery.budgets.usd_per_day"]}
            />
            <NumberField
              section="budgets"
              path="discovery.budgets.per_ask"
              label="Per research ask"
              unit="$"
              help={'"Research this" and Ask Scout'}
              bounds={bounds["discovery.budgets.per_ask"]}
            />
            <SelectField
              section="budgets"
              path="discovery.autonomy.bets"
              label="Bets from opportunities"
              options={BETS}
            />
          </div>
        </SectionCard>
      )}
      <FieldGroups section="budgets" groups={AUTONOMY_GROUPS} testId="steer-autonomy-fields" />
      <AutonomyLevels
        levels={steer.definition.autonomy_levels ?? []}
        current={String(getPath(steer.definition.effective, "autonomy.level") ?? "careful")}
      />
    </div>
  );
}
