import { ArrowUpRightIcon, MailIcon } from "lucide-react";
import type { FactoryInfo } from "@/lib/factory";
import type { FactorySender, ParsedFactoryMessage } from "@/lib/factoryMessages";
import { cn } from "@/lib/utils";
import { FactoryRoleChip } from "@/shell/FactoryBadge";
import { CARD_LINK_CLASS, FactoryLink, sessionHref } from "./FactoryTranscriptContext";
import { FoldableMarkdown } from "./FoldableMarkdown";
import { TONE_PILL, clockTime, messageKindTone, roleChipTone } from "./factoryTone";

/** Outer shell shared by the inbound factory cards. */
export const FACTORY_CARD_CLASS =
  "not-prose flex w-full min-w-0 flex-col gap-1.5 rounded-lg border border-border bg-card px-3 py-2.5 text-ui";

/** Header row of a factory card: wraps on narrow columns instead of overflowing. */
export const FACTORY_CARD_HEADER_CLASS =
  "flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-sm text-muted-foreground";

function senderInfo(from: FactorySender): FactoryInfo {
  return {
    role: from.role === "member" ? null : from.role,
    project: from.project,
    parent: null,
    summary: null,
    health: null,
  };
}

/** Small rounded pill (envelope kind, verdict, status). */
export function KindPill({
  label,
  tone,
  className,
}: {
  label: string;
  tone: keyof typeof TONE_PILL;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-full border px-2 text-sm leading-none",
        TONE_PILL[tone],
        className,
      )}
    >
      {label}
    </span>
  );
}

/**
 * Card for a `[Factory message · from "<title>" (<id>, <role>…) · kind=…]`
 * envelope: sender with its role chip, the kind, the time, a link to the
 * sender's session, and the body as markdown folded past 8 lines.
 */
export function FactoryMessageCard({
  parsed,
  createdAtS,
}: {
  parsed: ParsedFactoryMessage;
  createdAtS?: number;
}) {
  const from = parsed.from;
  if (!from) return null;
  const time = clockTime(createdAtS);
  const kind = parsed.msgKind ?? "info";
  return (
    <div
      className={FACTORY_CARD_CLASS}
      data-testid="factory-msg-card"
      data-msg-kind={kind}
      data-sender-role={from.role}
      data-mail-id={parsed.mailId}
    >
      <div className={FACTORY_CARD_HEADER_CLASS}>
        <MailIcon className="size-3.5 shrink-0" aria-hidden />
        <span className="sr-only">Factory message from</span>
        <span
          className="min-w-0 max-w-full truncate font-medium text-foreground"
          title={from.title}
        >
          {from.title}
        </span>
        <FactoryRoleChip
          info={senderInfo(from)}
          shownTitle={from.title}
          className={roleChipTone(from.role)}
        />
        <KindPill label={kind} tone={messageKindTone(kind)} />
        {time && <span className="shrink-0 tabular-nums">{time}</span>}
        <FactoryLink
          to={sessionHref(from.id)}
          className={cn(CARD_LINK_CLASS, "ml-auto max-w-[14rem]")}
          title={`Open ${from.title}`}
          testId="factory-msg-open-sender"
        >
          <span className="truncate">Open {from.title}</span>
          <ArrowUpRightIcon className="size-3.5 shrink-0" aria-hidden />
        </FactoryLink>
      </div>
      {parsed.body && <FoldableMarkdown text={parsed.body} testId="factory-msg-body" />}
    </div>
  );
}
