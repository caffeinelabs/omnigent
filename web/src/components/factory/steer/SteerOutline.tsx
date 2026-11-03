/**
 * Steer's outline: the sections in their groups (How it works · Product and
 * strategy · Discovery method · Teams and roles · Guardrails · History), each
 * row stating its current value in one line, an amber count where something
 * waits for an owner and a violet "staged" chip where something is staged.
 * On a phone it is one scrolling row of chips.
 */

import { LockIcon } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { type SteerSection, steerPageHref } from "../factoryRoutes";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import { StagedChip } from "./steerFields";
import { SECTION_INFO } from "./steerModel";

export interface SteerOutlineProps {
  factoryId: string;
  active: SteerSection;
  groups: readonly { title: string; sections: readonly SteerSection[] }[];
  /** Each section's current value in one line. */
  values: Partial<Record<SteerSection, string>>;
  staged: ReadonlySet<SteerSection>;
  /** Things waiting for an owner, per section. */
  waiting: Partial<Record<SteerSection, number>>;
}

export function SteerOutline({
  factoryId,
  active,
  groups,
  values,
  staged,
  waiting,
}: SteerOutlineProps) {
  const nav = useRef<HTMLElement>(null);
  // On a phone the outline is one row of chips: keep the current one in view (only the row scrolls).
  useEffect(() => {
    const box = nav.current;
    const current = box?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!box || !current || box.scrollWidth <= box.clientWidth) return;
    const outer = box.getBoundingClientRect();
    const inner = current.getBoundingClientRect();
    if (inner.left < outer.left) box.scrollLeft -= outer.left - inner.left + 16;
    else if (inner.right > outer.right) box.scrollLeft += inner.right - outer.right + 16;
  }, [active]);
  return (
    <nav
      ref={nav}
      aria-label="Steer sections"
      data-testid="steer-outline"
      className="flex min-w-0 gap-1 md:sticky md:top-2 md:w-60 md:shrink-0 md:flex-col max-md:-mx-1 max-md:overflow-x-auto max-md:px-1 max-md:pb-1 max-md:[scrollbar-width:none]"
    >
      {groups.map((group) => (
        <div key={group.title} className="contents md:flex md:flex-col md:gap-0.5">
          <h2 className="px-2 pt-3 pb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase first:pt-0 max-md:hidden">
            {group.title}
          </h2>
          {group.sections.map((section) => {
            const on = section === active;
            const count = waiting[section] ?? 0;
            const isStaged = staged.has(section);
            return (
              <Link
                key={section}
                to={steerPageHref(factoryId, section)}
                componentId={`factory.steer.nav.${section}`}
                data-testid={`steer-nav-${section}`}
                data-staged={isStaged ? "true" : undefined}
                aria-current={on ? "page" : undefined}
                className={cn(
                  "flex shrink-0 flex-col rounded-lg px-2 py-1.5 text-sm",
                  "max-md:h-9 max-md:flex-row max-md:items-center max-md:gap-1.5 max-md:border max-md:border-border max-md:px-3",
                  FOCUS_RING,
                  on
                    ? "bg-muted font-medium text-foreground max-md:border-foreground/30"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
              >
                <span className="flex min-w-0 items-center gap-1.5 whitespace-nowrap text-foreground">
                  {section === "gates" && (
                    <LockIcon aria-hidden className="size-3 shrink-0 text-muted-foreground" />
                  )}
                  {SECTION_INFO[section].label}
                  {count > 0 && (
                    <span
                      aria-label={`${count} waiting for an owner`}
                      className={cn(
                        "ml-auto inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-xs font-semibold tabular-nums",
                        MEANING.needsYou.surface,
                        MEANING.needsYou.text,
                      )}
                    >
                      {count}
                    </span>
                  )}
                  {isStaged && <StagedChip className={count > 0 ? "" : "ml-auto"} />}
                </span>
                <span className="truncate text-xs font-normal text-muted-foreground max-md:hidden">
                  {values[section] ?? SECTION_INFO[section].hint}
                </span>
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
