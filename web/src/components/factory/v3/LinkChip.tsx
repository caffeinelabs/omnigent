// Links between nodes are chips, never lines: kind · target · its state. A
// chip opens its target (Back returns); a count badge ("3 met links") opens a
// list of chips grouped under breadcrumbs. At most three chips show; "+n"
// opens the rest.

import { useState } from "react";
import { cn } from "@/lib/utils";
import { NodeLink } from "./nodeParts";
import type { Chip, ChipGroup } from "./nodeTypes";

const CHIP =
  "inline-flex max-w-full items-center gap-1 overflow-hidden rounded-full border px-2.5 py-px text-xs text-ellipsis whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-status-blue";

function toneClass(tone: Chip["tone"]): string {
  switch (tone) {
    case "amb":
      return "border-status-yellow/55 bg-status-yellow/12 text-status-yellow-fg";
    case "warn":
      return "border-dashed border-status-yellow/70 bg-card-solid text-status-yellow-fg";
    case "acc":
      return "border-status-blue/45 bg-status-blue/12 text-status-blue-fg";
    case "try":
      return "border-status-green/70 bg-status-green/12 text-status-green-fg";
    default:
      return "border-border bg-card-solid text-muted-foreground hover:border-border-strong hover:text-foreground";
  }
}

function ChipBody({ chip }: { chip: Chip }) {
  const quiet = chip.tone ? "opacity-80" : "text-foreground-tertiary";
  // A chip wider than its row ends in "…" (its parts shrink; the kind stays).
  return (
    <>
      {chip.kind && <span className={cn(quiet, "shrink-0")}>{chip.kind}</span>}
      <span className="min-w-0 truncate">{chip.title}</span>
      {chip.state && <span className={cn(quiet, "min-w-0 truncate")}>· {chip.state}</span>}
    </>
  );
}

/** One link chip: opens its target, or (a count badge) toggles its list. */
export function LinkChip({
  chip,
  open,
  onToggle,
}: {
  chip: Chip;
  open?: boolean;
  onToggle?: () => void;
}) {
  if (chip.ref && !chip.list) {
    return (
      <NodeLink
        nodeRef={chip.ref}
        title={chip.reason}
        data-testid="v3-chip"
        data-kind={chip.kind}
        className={cn(CHIP, toneClass(chip.tone))}
      >
        <ChipBody chip={chip} />
      </NodeLink>
    );
  }
  if (chip.list || onToggle) {
    return (
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        title={chip.reason}
        data-testid="v3-chip"
        data-kind={chip.kind}
        className={cn(CHIP, "cursor-pointer", toneClass(chip.tone))}
      >
        <ChipBody chip={chip} />
      </button>
    );
  }
  return (
    <span
      title={chip.reason}
      data-testid="v3-chip"
      data-kind={chip.kind}
      className={cn(CHIP, toneClass(chip.tone))}
    >
      <ChipBody chip={chip} />
    </span>
  );
}

function chipKey(c: Chip): string {
  return `${c.kind ?? ""}:${c.ref ?? ""}:${c.title}`;
}

function GroupList({ groups }: { groups: ChipGroup[] }) {
  return (
    <div
      data-testid="v3-chip-list"
      className="mt-0.5 rounded-lg border border-border bg-card-solid px-2.5 py-1.5 text-[12.5px]"
    >
      {groups.map((g) => (
        <div key={g.crumb ?? g.chips.map(chipKey).join("|")}>
          {g.crumb && <div className="mt-1 text-[11.5px] text-foreground-tertiary">{g.crumb}</div>}
          {g.chips.map((c) => (
            <div key={chipKey(c)} className="flex flex-wrap items-center gap-2 py-0.5">
              <LinkChip chip={c} />
            </div>
          ))}
          {g.note && <div className="mt-1.5 text-[11.5px] text-foreground-tertiary">{g.note}</div>}
        </div>
      ))}
    </div>
  );
}

/** The header's chips (at most three), "+n" for the rest, and the list either opens. */
export function ChipList({
  chips,
  more = 0,
  moreGroups,
}: {
  chips: Chip[];
  more?: number;
  moreGroups?: ChipGroup[];
}) {
  const [open, setOpen] = useState<number | "more" | null>(null);
  if (!chips.length) return null;
  const toggle = (which: number | "more") => setOpen((cur) => (cur === which ? null : which));
  const groups =
    open === "more" ? (moreGroups ?? []) : open != null ? (chips[open]?.list ?? []) : [];
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap gap-1.5" data-testid="v3-chips">
        {chips.slice(0, 3).map((chip) => {
          const at = chips.indexOf(chip);
          return (
            <LinkChip
              key={chipKey(chip)}
              chip={chip}
              open={open === at}
              onToggle={chip.list ? () => toggle(at) : undefined}
            />
          );
        })}
        {more > 0 && moreGroups?.length ? (
          <LinkChip
            chip={{ title: `+${more}` }}
            open={open === "more"}
            onToggle={() => toggle("more")}
          />
        ) : more > 0 ? (
          // A server without `chips_more`: the count alone, nothing to open.
          <LinkChip chip={{ title: `+${more}` }} />
        ) : null}
      </div>
      {groups.length > 0 && <GroupList groups={groups} />}
    </div>
  );
}
