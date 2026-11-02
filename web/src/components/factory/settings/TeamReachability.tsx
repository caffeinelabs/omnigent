/**
 * Owners' view of who Slack DMs reach: "3 of 4 people reachable", one row
 * per person with role, link status (and why it broke), how many event
 * groups they get, whether their quiet hours hold now and their last DM.
 * Never a Slack member ID: owners cannot link or change anyone's account.
 */

import type { NotifyTeamRow } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { TONE_CHIP, formatElapsed } from "../factoryFormat";
import { LINK_STATUS_TEXT, shortName } from "../notify/notifyModel";
import { useNotifyTeam } from "../notify/notifyQueries";

const STATUS_TONE = {
  verified: TONE_CHIP.pass,
  pending: TONE_CHIP.pending,
  error: TONE_CHIP.fail,
  unlinked: TONE_CHIP.muted,
} as const;

function statusText(row: NotifyTeamRow): string {
  if (row.slack_status === "verified" && !row.enabled) return "linked · DMs off";
  if (row.slack_status === "error") return `broken · ${row.error ?? "error"}`;
  return LINK_STATUS_TEXT[row.slack_status];
}

export function TeamRow({ row, nowS }: { row: NotifyTeamRow; nowS: number }) {
  const last =
    row.last_dm_at != null ? `last DM ${formatElapsed(nowS - row.last_dm_at)} ago` : "no DM yet";
  return (
    <li
      data-testid={`notify-team-${row.user_id}`}
      data-reachable={row.reachable ? "true" : "false"}
      className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 py-2 text-sm first:pt-0 last:pb-0"
    >
      <span className="min-w-0 truncate font-medium" title={row.user_id}>
        {shortName(row.user_id)}
      </span>
      <span className="text-muted-foreground">{row.role}</span>
      <span
        className={cn(
          "inline-flex h-5 max-w-full min-w-0 items-center truncate rounded-sm px-1.5 text-xs font-medium",
          STATUS_TONE[row.slack_status],
        )}
      >
        {statusText(row)}
      </span>
      <span className="ml-auto flex min-w-0 flex-wrap items-center gap-x-2 text-xs text-muted-foreground max-sm:ml-0 max-sm:w-full">
        <span>
          {row.groups_on.length} of 8 groups{row.digest ? " · digest" : ""}
        </span>
        {row.quiet_now && <span>· quiet now</span>}
        <span>· {last}</span>
        {row.last_error && row.last_error !== row.error && (
          <span className="text-destructive">· {row.last_error}</span>
        )}
      </span>
    </li>
  );
}

export function TeamReachability({ factoryId, nowS }: { factoryId: string; nowS: number }) {
  const team = useNotifyTeam(factoryId);
  return (
    <section
      data-testid="notify-team"
      aria-label="Team"
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-2">
        <h2 className="text-ui font-semibold">Team</h2>
        {team.data && (
          <span data-testid="notify-team-count" className="text-sm text-muted-foreground">
            {team.data.reachable} of {team.data.count} {team.data.count === 1 ? "person" : "people"}{" "}
            reachable by Slack DM
          </span>
        )}
      </div>
      {team.isLoading ? (
        <div aria-busy="true" className="h-16 animate-pulse rounded-lg bg-muted/70" />
      ) : team.error ? (
        <p className="text-sm text-destructive">Could not load the team</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {(team.data?.data ?? []).map((row) => (
            <TeamRow key={row.user_id} row={row} nowS={nowS} />
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">
        Each person links their own Slack account here, under Notifications.
      </p>
    </section>
  );
}
