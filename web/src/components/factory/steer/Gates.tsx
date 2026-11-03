/**
 * Steer › Gates (read only, Tier 0): each gate a pull request passes before
 * a person merges it, when it is required, the role a team needs for it and
 * what it checks, from `GET …/gates`. This is why some team edits are locked.
 * A server without the route still shows the roles the catalog says gates need.
 */

import { LockIcon } from "lucide-react";
import { SectionCard } from "../project/projectParts";
import { useSteer } from "./SteerContext";
import { TierZero } from "./steerFields";
import { useFactoryGates, useSteerCatalog } from "./steerQueries";

export function Gates() {
  const steer = useSteer();
  const gates = useFactoryGates(steer.factoryId);
  const catalog = useSteerCatalog(steer.factoryId, gates.data === null);
  if (gates.isLoading)
    return <div aria-busy="true" className="h-48 animate-pulse rounded-xl bg-muted/70" />;
  const data = gates.data;
  return (
    <div data-testid="steer-gates" className="flex min-w-0 flex-col gap-4">
      <SectionCard
        title="Gates"
        meta={
          <span className="inline-flex items-center gap-1">
            <LockIcon aria-hidden className="size-3" />
            Tier 0 · read only
          </span>
        }
        testId="steer-gates-table"
      >
        {data ? (
          <>
            <ul className="flex min-w-0 flex-col divide-y divide-border/60 md:hidden">
              {data.gates.map((g) => (
                <li
                  key={g.gate}
                  data-testid={`steer-gate-${g.gate}`}
                  className="flex flex-col gap-0.5 py-2 text-sm"
                >
                  <b className="font-medium">{g.label}</b>
                  <span className="text-xs text-muted-foreground">
                    {g.required_text}
                    {g.role_label ? ` · needs a ${g.role_label}` : ""}
                  </span>
                  <span className="text-foreground/80">{g.checks}</span>
                </li>
              ))}
            </ul>
            <table className="w-full text-sm max-md:hidden">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th scope="col" className="py-1.5 pr-3 font-medium">
                    Gate
                  </th>
                  <th scope="col" className="py-1.5 pr-3 font-medium">
                    Required
                  </th>
                  <th scope="col" className="py-1.5 pr-3 font-medium">
                    Needs the role
                  </th>
                  <th scope="col" className="py-1.5 font-medium">
                    What it checks
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.gates.map((g) => (
                  <tr
                    key={g.gate}
                    data-testid={`steer-gate-${g.gate}`}
                    className="border-b border-border/60 align-top last:border-0"
                  >
                    <td className="py-1.5 pr-3 font-medium whitespace-nowrap">{g.label}</td>
                    <td className="py-1.5 pr-3 text-muted-foreground">{g.required_text}</td>
                    <td className="py-1.5 pr-3">{g.role_label ?? "—"}</td>
                    <td className="py-1.5 text-foreground/80">
                      {g.checks}
                      {g.skipped_by.length > 0 && (
                        <span className="block text-xs text-muted-foreground">
                          skipped with {g.skipped_by.join(" or ")}
                        </span>
                      )}
                      {g.allowed_by && (
                        <span className="block text-xs text-muted-foreground">
                          a person allows it with {g.allowed_by}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.team_needs.some((n) => !n.pr_gate) && (
              <p className="text-sm text-muted-foreground">
                Also on every team:{" "}
                {data.team_needs
                  .filter((n) => !n.pr_gate)
                  .map((n) => n.text)
                  .join("; ")}
                .
              </p>
            )}
            <TierZero>{data.note}</TierZero>
          </>
        ) : (
          <>
            <ul className="flex flex-col gap-1 text-sm">
              {(catalog.data?.gates ?? []).map((g) => (
                <li key={g.gate}>
                  <b className="font-medium">{g.gate}</b>: {g.text}
                </li>
              ))}
            </ul>
            <TierZero>
              Gates are Tier 0. A project cannot lose the only member a gate needs, which is why
              some team edits are locked.
            </TierZero>
          </>
        )}
      </SectionCard>
    </div>
  );
}
