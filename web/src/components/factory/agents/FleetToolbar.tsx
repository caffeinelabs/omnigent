/**
 * Above the fleet: the counts, the filters (project, role, state, harness,
 * flags) and saved views, all of which write the free-text query, the query
 * box itself, and the grouping. Below it, once agents are selected, the bulk
 * bar: Message, Interrupt, Redispatch, Reassign and Show on Timeline.
 */

import type { ReactNode } from "react";
import { ChevronDownIcon, SearchIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { plural } from "../factoryFormat";
import { LeverGated } from "../shell/viewerCan";
import type { FleetCounts, FleetGroupBy } from "./fleetModel";
import {
  SAVED_VIEWS,
  queryFieldValue,
  queryHasTerm,
  setQueryField,
  toggleQueryTerm,
} from "./fleetQuery";
import type { InterventionKind } from "./interventionModel";

export interface FilterOption {
  value: string;
  label: string;
}

function FilterMenu({
  field,
  label,
  options,
  query,
  onQuery,
}: {
  field: string;
  label: string;
  options: FilterOption[];
  query: string;
  onQuery: (next: string) => void;
}) {
  const value = queryFieldValue(query, field);
  const current = options.find((o) => o.value.toLowerCase() === value?.toLowerCase());
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant={value ? "secondary" : "outline"}
          size="xs"
          data-testid={`fleet-filter-${field}`}
          componentId={`factory.fleet.filter_${field}`}
          className="max-w-44"
        >
          <span className="truncate">
            {current ? `${label}: ${current.label}` : value ? `${label}: ${value}` : label}
          </span>
          <ChevronDownIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 overflow-y-auto">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup
          value={value ?? ""}
          onValueChange={(next) => onQuery(setQueryField(query, field, next || null))}
        >
          <DropdownMenuRadioItem value="">Any</DropdownMenuRadioItem>
          {options.map((option) => (
            <DropdownMenuRadioItem key={option.value} value={option.value}>
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const GROUPS: { value: FleetGroupBy; label: string }[] = [
  { value: "project", label: "Project" },
  { value: "role", label: "Role" },
  { value: "none", label: "None" },
];

export function FleetToolbar({
  counts,
  query,
  onQuery,
  errors,
  groupBy,
  onGroupBy,
  filters,
  live,
}: {
  counts: FleetCounts;
  query: string;
  onQuery: (next: string) => void;
  errors: string[];
  groupBy: FleetGroupBy;
  onGroupBy: (next: FleetGroupBy) => void;
  filters: { field: string; label: string; options: FilterOption[] }[];
  /** `live · polled 4s ago`, or why it isn't. */
  live: ReactNode;
}) {
  return (
    <div data-testid="fleet-toolbar" className="flex min-w-0 flex-col gap-2">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-base font-semibold">
          Agents{" "}
          <span className="font-normal text-muted-foreground tabular-nums">· {counts.total}</span>
        </h1>
        <span data-testid="fleet-counts" className="text-sm text-muted-foreground tabular-nums">
          {counts.running} running · {counts.waiting} waiting · {counts.dead} dead ·{" "}
          {counts.flagged} flagged
        </span>
        <span className="ml-auto text-xs text-muted-foreground">{live}</span>
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        {filters.map((filter) => (
          <FilterMenu key={filter.field} {...filter} query={query} onQuery={onQuery} />
        ))}
        <span className="mx-1 h-4 w-px bg-border max-sm:hidden" aria-hidden />
        {/* On a phone the saved views take their own line, still labelled. */}
        <span className="flex min-w-0 flex-wrap items-center gap-1.5 max-sm:basis-full max-sm:gap-1">
          <span className="text-xs text-muted-foreground">Saved</span>
          {SAVED_VIEWS.map((view) => {
            const term = `is:${view.key}`;
            const on = queryHasTerm(query, term);
            return (
              <Button
                key={view.key}
                size="xs"
                variant={on ? "secondary" : "ghost"}
                aria-pressed={on}
                title={view.hint}
                data-testid={`fleet-view-${view.key}`}
                componentId={`factory.fleet.view_${view.key}`}
                onClick={() => onQuery(toggleQueryTerm(query, term))}
                className={cn("max-sm:px-1.5", !on && "text-muted-foreground")}
              >
                {view.label}
              </Button>
            );
          })}
        </span>
      </div>
      <div className="flex min-w-0 items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <SearchIcon
            aria-hidden
            className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="state:running tool:Bash cost>2 project:db-*"
            aria-label="Filter agents"
            aria-invalid={errors.length > 0 || undefined}
            data-testid="fleet-query"
            spellCheck={false}
            autoComplete="off"
            className="h-8 pr-7 pl-7 font-mono text-sm"
          />
          {query && (
            <button
              type="button"
              aria-label="Clear the filter"
              data-testid="fleet-query-clear"
              onClick={() => onQuery("")}
              className="absolute top-1/2 right-1.5 inline-flex size-5 -translate-y-1/2 cursor-pointer items-center justify-center rounded-sm text-muted-foreground hover:text-foreground"
            >
              <XIcon className="size-3.5" />
            </button>
          )}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="xs"
              className="h-8 shrink-0"
              componentId="factory.fleet.group"
              data-testid="fleet-group-by"
            >
              <span className="max-sm:hidden">Group:</span>{" "}
              {GROUPS.find((g) => g.value === groupBy)?.label}
              <ChevronDownIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuRadioGroup
              value={groupBy}
              onValueChange={(v) => onGroupBy(v as FleetGroupBy)}
            >
              {GROUPS.map((g) => (
                <DropdownMenuRadioItem key={g.value} value={g.value}>
                  {g.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {errors.length > 0 && (
        <p data-testid="fleet-query-errors" className="text-xs text-warning">
          Not understood: <span className="font-mono">{errors.join(" ")}</span> · try{" "}
          <span className="font-mono">state:running</span>,{" "}
          <span className="font-mono">cost&gt;2</span>,{" "}
          <span className="font-mono">idle&gt;10m</span> or{" "}
          <span className="font-mono">is:looping</span>
        </p>
      )}
    </div>
  );
}

export function BulkBar({
  count,
  hidden = 0,
  withTask,
  onAction,
  onTimeline,
  onClear,
}: {
  count: number;
  /** Selected agents the current filter hides (the levers leave them out). */
  hidden?: number;
  /** Selected agents that work on a task (Redispatch and Reassign act on tasks). */
  withTask: number;
  onAction: (kind: InterventionKind) => void;
  onTimeline: () => void;
  onClear: () => void;
}) {
  if (count === 0 && hidden === 0) return null;
  const lever = (kind: InterventionKind, label: string, disabled = false) => (
    <LeverGated lever="intervene">
      <Button
        size="xs"
        variant={kind === "interrupt" ? "destructive" : "outline"}
        disabled={disabled}
        onClick={() => onAction(kind)}
        data-testid={`fleet-bulk-${kind}`}
        componentId={`factory.fleet.bulk_${kind}`}
      >
        {label}
      </Button>
    </LeverGated>
  );
  return (
    <div
      role="toolbar"
      aria-label="Selected agents"
      data-testid="fleet-bulk-bar"
      // The page scroller already pads for the phone's bottom tabs.
      className="sticky bottom-2 z-20 mb-2 flex min-w-0 flex-wrap items-center gap-1.5 rounded-xl border border-border bg-background/95 px-3 py-2 shadow-sm backdrop-blur-md"
    >
      <span data-testid="fleet-bulk-count" className="mr-1 text-sm font-medium tabular-nums">
        {plural(count, "agent")} selected
        {hidden > 0 && (
          <span className="font-normal text-muted-foreground">
            {" "}
            ({hidden} hidden by the filter)
          </span>
        )}
      </span>
      {lever("message", "Message…", count === 0)}
      {lever("interrupt", "Interrupt", count === 0)}
      {lever("redispatch", "Redispatch task", withTask === 0)}
      {lever("reassign", "Reassign…", withTask === 0)}
      <Button
        size="xs"
        variant="ghost"
        disabled={count === 0}
        onClick={onTimeline}
        data-testid="fleet-bulk-timeline"
        componentId="factory.fleet.bulk_timeline"
      >
        Show on Timeline
      </Button>
      <span className="ml-auto text-xs text-muted-foreground max-md:hidden">
        Every action notifies the PL
      </span>
      <Button
        size="xs"
        variant="ghost"
        onClick={onClear}
        data-testid="fleet-bulk-clear"
        componentId="factory.fleet.bulk_clear"
      >
        Clear
      </Button>
    </div>
  );
}
