/**
 * A Room message's body. Plain text renders with its tokens as live chips
 * (`#db-version`, `!9631`, `D-7`, `@pl:db-version`, `@mia@…`) plus inline
 * `code`; a body with block markdown (fences, lists, tables, headings)
 * renders as markdown, its refs as a chip row underneath. A token that names
 * nothing here ("shortlist #2") stays plain text.
 */

import { type ReactNode, useMemo } from "react";
import { type RoomToken, findRoomTokens } from "@/lib/composerMentions";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { TONE_DOT } from "../factoryFormat";
import { FoldableMarkdown } from "../transcript/FoldableMarkdown";
import { type RefSources, type RefView, refView } from "./roomRefs";

const BLOCK_MARKDOWN = /(^|\n)(```|\s*[-*] |\s*\d+\. |#{1,6} |\|.*\|)/;

/** Whether a body needs the markdown renderer (block syntax), not inline chips. */
export function isBlockMarkdown(body: string): boolean {
  return BLOCK_MARKDOWN.test(body);
}

export function RefChip({ token, view }: { token: RoomToken; view: RefView }) {
  const className = cn(
    "inline-flex max-w-full items-baseline gap-1 rounded-[5px] px-1 align-baseline font-mono text-[0.92em] leading-snug whitespace-nowrap",
    view.self
      ? "bg-warning/15 text-foreground"
      : "bg-muted text-foreground hover:bg-muted/70 dark:bg-muted/70",
  );
  const body = (
    <>
      {token.kind !== "person" && token.kind !== "thread" && (
        <span
          aria-hidden
          className={cn(
            "relative -top-px inline-block size-1.5 shrink-0 self-center rounded-full",
            TONE_DOT[view.tone],
          )}
        />
      )}
      <span className="truncate">{view.label}</span>
    </>
  );
  return view.href ? (
    <Link
      to={view.href}
      title={view.title}
      componentId="factory.room.ref_chip"
      data-testid={`room-ref-${token.text}`}
      data-kind={token.kind}
      className={className}
    >
      {body}
    </Link>
  ) : (
    <span
      title={view.title}
      data-testid={`room-ref-${token.text}`}
      data-kind={token.kind}
      className={className}
    >
      {body}
    </span>
  );
}

/** Inline `code` spans of a plain-text run starting at `offset` of the body. */
function withCode(text: string, offset: number): ReactNode[] {
  const out: ReactNode[] = [];
  let at = offset;
  for (const part of text.split(/(`[^`\n]+`)/g)) {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      out.push(
        <code key={`c${at}`} className="rounded bg-muted px-1 font-mono text-[0.92em]">
          {part.slice(1, -1)}
        </code>,
      );
    } else if (part) {
      out.push(<span key={`t${at}`}>{part}</span>);
    }
    at += part.length;
  }
  return out;
}

export function RoomText({
  body,
  sources,
  className,
  testId,
}: {
  body: string;
  sources: RefSources;
  className?: string;
  testId?: string;
}) {
  const tokens = useMemo(() => findRoomTokens(body), [body]);
  // Only tokens that resolve become chips; the rest read as the text they are.
  const refs = tokens.flatMap((token) => {
    const view = refView(token, sources);
    return view ? [{ token, view }] : [];
  });
  if (isBlockMarkdown(body)) {
    const unique = refs.filter(
      (r, i) => refs.findIndex((u) => u.token.text === r.token.text) === i,
    );
    return (
      <div className={cn("flex min-w-0 flex-col gap-1", className)} data-testid={testId}>
        <FoldableMarkdown text={body} className="text-ui" />
        {unique.length > 0 && (
          <p className="flex flex-wrap gap-1">
            {unique.map(({ token, view }) => (
              <RefChip key={token.text} token={token} view={view} />
            ))}
          </p>
        )}
      </div>
    );
  }
  const out: ReactNode[] = [];
  let at = 0;
  for (const { token, view } of refs) {
    if (token.start > at) out.push(...withCode(body.slice(at, token.start), at));
    out.push(<RefChip key={`r${token.start}`} token={token} view={view} />);
    at = token.end;
  }
  if (at < body.length) out.push(...withCode(body.slice(at), at));
  return (
    <p
      data-testid={testId}
      className={cn("min-w-0 text-ui break-words whitespace-pre-wrap", className)}
    >
      {out}
    </p>
  );
}
