/**
 * Steer › Budgets and autonomy: money (per project, per day, the spend
 * alert), the loops' attempts and rounds, discovery's budget while it runs,
 * and who decides what (project proposals, bets from opportunities, the
 * engine's mode). Lowering a money budget only reduces spend, so owners and
 * editors apply it at once (spec §8.4); raising one, and the loops and
 * autonomy, are owner changes that editors propose. The change column's
 * preview says which. Every field stages.
 */

import { SectionCard } from "../project/projectParts";
import { AUTONOMY_GROUPS, BUDGET_GROUPS } from "../settings/settingsModel";
import { useSteer } from "./SteerContext";
import { FieldGroups, NumberField, SelectField } from "./steerFields";

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
    </div>
  );
}
