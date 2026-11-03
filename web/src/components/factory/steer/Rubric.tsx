/**
 * Steer › Scoring rubric: how opportunities rank. The formula is code (Tier
 * 0); every number is data: fit outside the focus segments, the Now and Next
 * cut-offs, what "moderate" evidence needs, reach per complaining account,
 * evidence half-life, intensity weights and the thresholds for a bet. Each
 * slider stages, and the change column shows the re-rank before Apply. A
 * field a running trial changed is locked. How to check a change, and what
 * never runs as a trial, close the section.
 */

import { SectionCard } from "../project/projectParts";
import { useSteer } from "./SteerContext";
import { NumberField, SelectField, SliderField, TierZero } from "./steerFields";
import { rankText } from "./steerModel";

const RERANKS = "Re-scores every opportunity. See the re-rank in the right column.";

export function Rubric() {
  const steer = useSteer();
  const bounds = steer.definition.bounds;
  const pct = (v: number) => rankText(v);
  return (
    <div data-testid="steer-rubric" className="flex min-w-0 flex-col gap-4">
      <SectionCard
        title="Scoring rubric"
        meta={`part of definition v${steer.definition.def_version}`}
        testId="steer-rubric-numbers"
      >
        <p className="rounded-lg border border-border bg-muted/40 px-3 py-2 font-mono text-sm">
          rank = reach × intensity × fit × confidence
        </p>
        <p className="text-sm text-muted-foreground">
          The shape of the formula is code (Tier 0). Every number below is data you can change.
          Confidence comes from the server's evidence meter; no agent can type it.
        </p>
        <div className="flex min-w-0 flex-col">
          <SliderField
            section="rubric"
            path="discovery.rubric.off_focus_factor"
            label="Fit outside the focus segments"
            help="multiplies fit for opportunities outside focus"
            min={0}
            max={1}
            step={0.05}
            effect={RERANKS}
          />
          <SliderField
            section="rubric"
            path="discovery.rubric.bands.now"
            label="Now: score at least"
            help="opportunities above this reach Atlas first"
            min={0.05}
            max={0.5}
            step={0.01}
            format={pct}
            effect={RERANKS}
          />
          <SliderField
            section="rubric"
            path="discovery.rubric.bands.next"
            label="Next: score at least"
            help="below this is Later"
            min={0.02}
            max={0.3}
            step={0.01}
            format={pct}
            effect={RERANKS}
          />
          <SliderField
            section="rubric"
            path="discovery.rubric.moderate_min_accounts"
            label={'"Moderate" evidence needs accounts'}
            help="distinct accounts in the window"
            min={1}
            max={20}
            step={1}
            effect={RERANKS}
          />
          <SliderField
            section="rubric"
            path="discovery.rubric.moderate_min_source_types"
            label={'"Moderate" evidence needs source types'}
            help="stops one loud source from validating alone"
            min={1}
            max={4}
            step={1}
            effect={RERANKS}
          />
          <SliderField
            section="rubric"
            path="discovery.rubric.reach_ratio.low"
            label="Reach per complaining account"
            help="until analytics measures reach directly"
            min={1}
            max={60}
            step={1}
            effect={RERANKS}
          />
          <SliderField
            section="rubric"
            path="discovery.rubric.decay_half_life_days"
            label="Evidence half-life"
            help="how fast evidence ages"
            min={7}
            max={180}
            step={1}
            format={(v) => `${v} d`}
            effect={RERANKS}
          />
          <SliderField
            section="rubric"
            path="discovery.rubric.window_days"
            label="Evidence window"
            help="how far back signals count"
            min={7}
            max={365}
            step={1}
            format={(v) => `${v} d`}
            effect={RERANKS}
          />
        </div>
        <h3 className="mt-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          Intensity weights
        </h3>
        <div className="flex min-w-0 flex-col">
          <SliderField
            section="rubric"
            path="discovery.rubric.intensity.blocker"
            label="Blocker"
            help="cannot continue"
            min={0}
            max={1}
            step={0.05}
            effect={RERANKS}
          />
          <SliderField
            section="rubric"
            path="discovery.rubric.intensity.workaround"
            label="Workaround"
            help="gets there another way"
            min={0}
            max={1}
            step={0.05}
            effect={RERANKS}
          />
          <SliderField
            section="rubric"
            path="discovery.rubric.intensity.annoyance"
            label="Annoyance"
            help="irritated, continues"
            min={0}
            max={1}
            step={0.05}
            effect={RERANKS}
          />
          <SliderField
            section="rubric"
            path="discovery.rubric.intensity.nice"
            label="Nice to have"
            help="would like it"
            min={0}
            max={1}
            step={0.05}
            effect={RERANKS}
          />
        </div>
      </SectionCard>
      <SectionCard title="When an opportunity becomes a bet" testId="steer-thresholds">
        <div className="flex min-w-0 flex-col divide-y divide-border/60">
          <SelectField
            section="rubric"
            path="discovery.thresholds.validated_min_level"
            label="Evidence a bet needs at least"
            options={["hunch", "weak", "moderate", "strong", "proven"].map((v) => ({
              value: v,
              label: v,
            }))}
          />
          <NumberField
            section="rubric"
            path="discovery.thresholds.min_accounts"
            label="Accounts a bet needs at least"
            integer
            bounds={bounds["discovery.thresholds.min_accounts"]}
          />
          <NumberField
            section="rubric"
            path="discovery.thresholds.bets_per_week"
            label="Bets Atlas files per week, at most"
            integer
            bounds={bounds["discovery.thresholds.bets_per_week"]}
          />
          <NumberField
            section="rubric"
            path="discovery.thresholds.max_open_bets"
            label="Open bets at once, at most"
            integer
            bounds={bounds["discovery.thresholds.max_open_bets"]}
          />
        </div>
      </SectionCard>
      <SectionCard title="Checking a rubric change" testId="steer-rubric-check">
        <ul className="flex flex-col gap-1.5 text-sm">
          <li>
            <b className="font-semibold">Preview</b>: the re-rank in the right column, now.
          </li>
          <li className="text-muted-foreground">
            <b className="font-semibold">Backtest</b> against past decisions and{" "}
            <b className="font-semibold">shadow runs</b>: not available yet.
          </li>
          <li>
            <b className="font-semibold">Trial</b>: judged on discovery guards (quote failures 0,
            privacy events 0, accepted bets within −10 pp, cost per accepted bet within +20 %), once
            this server measures them. Until then a rubric change applies as a version you can
            restore; the change column says which.
          </li>
          <li>
            <b className="font-semibold">Outcomes</b>: readout win rate by version, once each
            version has enough readouts.
          </li>
        </ul>
      </SectionCard>
      <SectionCard title="Never on trial" meta="Tier 0" testId="steer-never-trial">
        <TierZero>
          Provenance and quote checks · redaction and the injection envelope · no outbound channel
          for agents that read user text · frozen measurement plans · the floor on small slices.
        </TierZero>
      </SectionCard>
    </div>
  );
}
