// Software-factory decorations for Agents-panel rows and graph nodes: a role
// chip (`PL · api-version`) and the member's beacon badge (summary + health).

import {
  factoryBeaconText,
  factoryRoleChipText,
  hasFactoryBeacon,
  isFactoryWarningHealth,
  type FactoryInfo,
} from "@/lib/factory";
import { cn } from "@/lib/utils";

/**
 * Role/project chip; renders nothing for a session without either label, or
 * when `shownTitle` (the row's visible title) already reads the same.
 */
export function FactoryRoleChip({
  info,
  shownTitle,
  className,
}: {
  info: FactoryInfo | null;
  shownTitle?: string | null;
  className?: string;
}) {
  const text = factoryRoleChipText(info, shownTitle);
  if (!text) return null;
  return (
    <span
      data-testid="subagent-factory-role"
      title={`Factory role: ${text}`}
      className={cn(
        "min-w-0 max-w-full shrink truncate rounded-sm border border-border px-1 font-mono text-[10px] leading-4 text-muted-foreground",
        className,
      )}
    >
      {text}
    </span>
  );
}

/**
 * Beacon badge: the member's self-reported summary, in the warning colour
 * when its health is degraded, blocked or dead. Renders nothing without a beacon.
 */
export function FactoryBadge({
  info,
  className,
}: {
  info: FactoryInfo | null;
  className?: string;
}) {
  if (!hasFactoryBeacon(info)) return null;
  const warn = isFactoryWarningHealth(info.health);
  const tooltip = [info.health ? `Health: ${info.health}` : null, info.summary]
    .filter(Boolean)
    .join(" — ");
  return (
    <span
      data-testid="subagent-factory-badge"
      data-health={info.health ?? undefined}
      data-warning={warn ? "true" : undefined}
      title={tooltip}
      className={cn(
        "inline-block max-w-full truncate rounded-sm px-1.5 text-xs leading-5",
        warn ? "bg-warning/15 text-warning" : "bg-muted text-muted-foreground",
        className,
      )}
    >
      {factoryBeaconText(info)}
    </span>
  );
}
