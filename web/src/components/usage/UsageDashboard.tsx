// Usage & cost dashboard, rendered inside the Usage settings section.
//
// Everyone sees their OWN spend; an admin additionally gets a scope selector
// to view every user's spend or drill into one user. The window (Today / 7d /
// 30d) and grouping dimension (Provider / Harness / Model) map directly to the
// `GET /v1/usage/summary` query params. One component serves both roles —
// `canViewOthers` just toggles the scope control on.

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CoinsIcon, LogInIcon, LogOutIcon } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useUsageSummary } from "@/hooks/useUsageSummary";
import { listUsers } from "@/lib/accountsApi";
import {
  formatTokensOrDash,
  formatUsd,
  groupByLabel,
  USAGE_USER_ALL,
  type UsageGroupBy,
  type UsageGroupEntry,
  type UsagePeriod,
} from "@/lib/usageApi";
import { UsageChart } from "@/components/usage/UsageChart";

// Scope selector values. "me" and "all" are UI sentinels; any other value is a
// concrete user id. Kept distinct from the wire param so "me" can omit `user`.
const SCOPE_ME = "me";

const PERIODS: { value: UsagePeriod; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7days", label: "7 days" },
  { value: "30days", label: "30 days" },
];

const GROUP_BYS: UsageGroupBy[] = ["provider", "harness", "model"];

export function UsageDashboard({ canViewOthers }: { canViewOthers: boolean }) {
  const [period, setPeriod] = useState<UsagePeriod>("7days");
  const [groupBy, setGroupBy] = useState<UsageGroupBy>("model");
  const [scope, setScope] = useState<string>(SCOPE_ME);

  // "me" omits the param (caller's own spend); "all" / a user id pass through.
  const user = scope === SCOPE_ME ? undefined : scope;

  const query = useUsageSummary({ period, groupBy, user });

  // Admin-only: the account list feeds the per-user drill-down. Accounts mode
  // only (returns null otherwise / for non-admins); the two sentinel scopes
  // still work regardless.
  const usersQuery = useQuery({
    queryKey: ["account-users"],
    queryFn: listUsers,
    enabled: canViewOthers,
    staleTime: 60_000,
  });
  const users = usersQuery.data ?? null;

  const summary = query.data;

  return (
    <div className="flex flex-col gap-6">
      {/* Controls: window + grouping (everyone), scope (admins). One row, wraps
          on narrow widths. */}
      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={period} onValueChange={(v: string) => setPeriod(v as UsagePeriod)}>
          <TabsList>
            {PERIODS.map((p) => (
              <TabsTrigger key={p.value} value={p.value}>
                {p.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        <Tabs value={groupBy} onValueChange={(v: string) => setGroupBy(v as UsageGroupBy)}>
          <TabsList>
            {GROUP_BYS.map((g) => (
              <TabsTrigger key={g} value={g}>
                {groupByLabel(g)}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        {canViewOthers && (
          <Select value={scope} onValueChange={setScope}>
            <SelectTrigger className="ml-auto w-48" aria-label="Whose usage">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={SCOPE_ME}>Your usage</SelectItem>
              <SelectItem value={USAGE_USER_ALL}>All users</SelectItem>
              {users !== null && users.length > 0 && <SelectSeparator />}
              {(users ?? []).map((u) => (
                <SelectItem key={u.id} value={u.id}>
                  {u.id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {query.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : query.isError ? (
        <div
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          Couldn't load usage. {(query.error as Error).message}
        </div>
      ) : summary ? (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <KpiTile
              label="Total cost"
              value={formatUsd(summary.total_cost_usd)}
              icon={<CoinsIcon className="size-4" />}
            />
            <KpiTile
              label="Input tokens"
              value={formatTokensOrDash(summary.total_input_tokens, summary.total_cost_usd)}
              icon={<LogInIcon className="size-4" />}
            />
            <KpiTile
              label="Output tokens"
              value={formatTokensOrDash(summary.total_output_tokens, summary.total_cost_usd)}
              icon={<LogOutIcon className="size-4" />}
            />
          </div>

          {summary.buckets.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Spend over time</CardTitle>
                <CardDescription>
                  USD cost per {period === "today" ? "hour" : "day"}.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <UsageChart buckets={summary.buckets} period={period} />
              </CardContent>
            </Card>
          )}

          <GroupsTable
            groupBy={groupBy}
            groups={summary.groups}
            totalCost={summary.total_cost_usd}
          />

          {/* Cost is a display figure; subscription-auth harnesses aren't
              billed per token, so their spend is notional. Native harnesses
              report a cost-only total with no token counts, hence the dash. */}
          <p className="text-xs text-muted-foreground">
            Costs are estimates. Subscription-based harnesses are billed by plan, not per token, so
            their spend is shown as an equivalent list-price estimate. A “—” in a token column means
            the harness reported cost but not token counts (native harnesses forward a cost-only
            total), so those tokens are unknown rather than zero.
          </p>
        </>
      ) : null}
    </div>
  );
}

function KpiTile({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardDescription className="flex items-center gap-1.5">
          <span className="text-muted-foreground">{icon}</span>
          {label}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
      </CardContent>
    </Card>
  );
}

function GroupsTable({
  groupBy,
  groups,
  totalCost,
}: {
  groupBy: UsageGroupBy;
  groups: UsageGroupEntry[];
  totalCost: number;
}) {
  // Server already sorts groups by cost desc; render in the order returned.
  if (groups.length === 0) {
    return <p className="text-sm text-muted-foreground">No usage in this window.</p>;
  }

  return (
    <div className="overflow-hidden rounded-md border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">{groupByLabel(groupBy)}</th>
            <th className="px-3 py-2 text-right font-medium">Input</th>
            <th className="px-3 py-2 text-right font-medium">Output</th>
            <th className="px-3 py-2 text-right font-medium">Cost</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((row) => {
            const share = totalCost > 0 ? (row.total_cost_usd / totalCost) * 100 : 0;
            return (
              <tr key={row.group_key} className="border-t border-border">
                <td className="px-3 py-2 align-middle">
                  {/* A thin share-of-total bar behind the label reads the
                      cost distribution at a glance without a second chart. */}
                  <div className="flex flex-col gap-1">
                    <span className="font-medium">{row.group_key}</span>
                    <span
                      aria-hidden
                      className="h-1 rounded-full bg-primary/70"
                      style={{ width: `${Math.max(share, share > 0 ? 2 : 0)}%` }}
                    />
                  </div>
                </td>
                <td className="px-3 py-2 text-right align-middle tabular-nums text-muted-foreground">
                  {formatTokensOrDash(row.input_tokens, row.total_cost_usd)}
                </td>
                <td className="px-3 py-2 text-right align-middle tabular-nums text-muted-foreground">
                  {formatTokensOrDash(row.output_tokens, row.total_cost_usd)}
                </td>
                <td className="px-3 py-2 text-right align-middle tabular-nums font-medium">
                  {formatUsd(row.total_cost_usd)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
