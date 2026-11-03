/**
 * Steer › Cadence and previews: today's knobs for how the factory runs
 * (heartbeats, the supervisor, mail rates, previews and the project cap).
 * They may run as a trial on delivery guards, as before. Models moved to
 * Roles and briefs.
 */

import { Link } from "@/lib/routing";
import { steerPageHref } from "../factoryRoutes";
import { useSteer } from "./SteerContext";
import { FieldGroups } from "./steerFields";
import { CADENCE_GROUPS } from "./steerModel";

export function Cadence() {
  const steer = useSteer();
  return (
    <div data-testid="steer-cadence" className="flex min-w-0 flex-col gap-4">
      <FieldGroups section="cadence" groups={CADENCE_GROUPS} testId="steer-cadence-fields" />
      <p className="text-sm text-muted-foreground">
        Default models per role are in{" "}
        <Link
          to={steerPageHref(steer.factoryId, "roles")}
          componentId="factory.steer.to_roles"
          className="text-status-blue-fg hover:underline"
        >
          Roles and briefs
        </Link>
        .
      </p>
    </div>
  );
}
