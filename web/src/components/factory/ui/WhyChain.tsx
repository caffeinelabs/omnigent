/**
 * Why a piece of work exists, as linked boxes from evidence to impact
 * (Evidence → Opportunity → Bet → Project → Readout). A link that does not
 * exist yet is drawn hatched with a dashed border and says what is missing,
 * so "no user evidence" and "not measured" are visible, not implied.
 */

import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { FOCUS_RING, MEANING, WHY_LINK_LABEL, type WhyLinkKind } from "./statusTokens";

export interface WhyLink {
  kind: WhyLinkKind;
  /** What the link holds ("23 accounts", "O-15 · moderate"), or what is absent ("none yet"). */
  value: string;
  detail?: string;
  /** The link does not exist yet: hatched, dashed. */
  missing?: boolean;
  /** Waits for a person (amber border). */
  waiting?: boolean;
  /** Part of the selected chain (blue). */
  lit?: boolean;
  /** Opens the linked object (a panel). */
  onSelect?: () => void;
  /** Navigates to the linked object. */
  to?: string;
}

const MISSING_FILL =
  "repeating-linear-gradient(135deg, transparent 0 6px, color-mix(in srgb, var(--status-gray) 22%, transparent) 6px 8px)";

export interface WhyChainProps {
  links: readonly WhyLink[];
  /** Stack the boxes top to bottom. */
  vertical?: boolean;
  /** Tighter boxes for side panels. */
  compact?: boolean;
  /** Names the chain for assistive tech. */
  label?: string;
  className?: string;
}

function LinkBody({ link, compact }: { link: WhyLink; compact: boolean }) {
  return (
    <>
      <span
        className={cn(
          "block text-sm font-semibold text-muted-foreground",
          compact ? "text-[11px]" : "tracking-wide uppercase",
        )}
      >
        {WHY_LINK_LABEL[link.kind]}
      </span>
      <span className={cn("block leading-snug font-semibold", compact ? "text-sm" : "text-ui")}>
        {link.value}
        {link.missing && <span className="sr-only"> (missing link)</span>}
      </span>
      {link.detail && <span className="block text-sm text-muted-foreground">{link.detail}</span>}
    </>
  );
}

function LinkBox({ link, compact }: { link: WhyLink; compact: boolean }) {
  const className = cn(
    "flex h-full w-full flex-col justify-start rounded-lg border bg-card-solid text-left [overflow-wrap:anywhere]",
    compact ? "px-1.5 py-1.5" : "px-2.5 py-1.5",
    link.missing ? "border-dashed border-border" : "border-border/70",
    link.waiting && !link.missing && MEANING.needsYou.border,
    link.lit && "border-status-blue ring-1 ring-status-blue",
    (link.onSelect || link.to) && "cursor-pointer hover:border-border-strong",
    (link.onSelect || link.to) && FOCUS_RING,
  );
  const style = link.missing ? { background: MISSING_FILL } : undefined;
  const body = <LinkBody link={link} compact={compact} />;
  const data = {
    "data-kind": link.kind,
    "data-missing": link.missing || undefined,
    "data-waiting": link.waiting || undefined,
  };
  if (link.to) {
    return (
      <Link to={link.to} className={className} style={style} {...data}>
        {body}
      </Link>
    );
  }
  if (link.onSelect) {
    return (
      <button type="button" onClick={link.onSelect} className={className} style={style} {...data}>
        {body}
      </button>
    );
  }
  return (
    <div className={className} style={style} {...data}>
      {body}
    </div>
  );
}

/** Stable keys even when two links share a kind and value ("none yet" twice). */
function linkKeys(links: readonly WhyLink[]): { link: WhyLink; key: string }[] {
  const seen = new Map<string, number>();
  return links.map((link) => {
    const base = `${link.kind}:${link.value}`;
    const repeat = seen.get(base) ?? 0;
    seen.set(base, repeat + 1);
    return { link, key: repeat ? `${base}#${repeat}` : base };
  });
}

export function WhyChain({
  links,
  vertical = false,
  compact = false,
  label = "Why it is here",
  className,
}: WhyChainProps) {
  const Arrow = vertical ? ChevronDownIcon : ChevronRightIcon;
  const arrow: ReactNode = (
    <li
      aria-hidden
      className={cn(
        "grid shrink-0 place-items-center text-foreground-tertiary",
        vertical ? "h-4" : "w-5 max-[760px]:hidden",
        compact && !vertical && "w-2.5",
      )}
    >
      <Arrow className="size-3.5" />
    </li>
  );
  return (
    <ol
      aria-label={label}
      className={cn(
        "m-0 flex list-none items-stretch p-0",
        vertical ? "flex-col" : "max-[760px]:flex-wrap max-[760px]:gap-1.5",
        className,
      )}
    >
      {linkKeys(links).map(({ link, key }, i) => (
        <Fragment key={key}>
          {i > 0 && arrow}
          <li
            className={cn(
              "min-w-0",
              !vertical && (compact ? "flex-1 basis-0" : "min-w-[120px] flex-1"),
              !vertical && "max-[760px]:min-w-[calc(50%-0.375rem)]",
            )}
          >
            <LinkBox link={link} compact={compact} />
          </li>
        </Fragment>
      ))}
    </ol>
  );
}
