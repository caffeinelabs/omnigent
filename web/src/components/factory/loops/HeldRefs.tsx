/**
 * "waiting on D-22 +1": the answers a running task waits on, each one a link.
 * The oldest links to its card on the Decisions page; `+N` links to the open
 * decisions. A task that waits on something else (a merge, another task, a
 * dependency) shows the server's words instead, unlinked. The fleet table, the
 * agent header and the snapshot loop chips draw it.
 */

import type { LoopHoldView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { decisionsPageHref } from "../factoryRoutes";
import { heldOnAnswer, heldTotal } from "./loopStripModel";

/** What a hold that is not a person's answer waits on, in the server's words. */
function HeldLabel({ held, className }: { held: LoopHoldView; className?: string }) {
  return (
    <span
      data-testid="held-label"
      className={className}
      title={held.who ? `${held.who} must act` : undefined}
    >
      {held.label}
    </span>
  );
}

export function HeldRefs({
  held,
  factoryId,
  componentId,
  className,
}: {
  held: LoopHoldView;
  factoryId: string;
  componentId: string;
  className?: string;
}) {
  if (!heldOnAnswer(held)) return <HeldLabel held={held} className={className} />;
  const more = heldTotal(held) - 1;
  const first = held.refs[0] ?? held.decision_ref;
  const link = "font-mono whitespace-nowrap underline-offset-2 hover:underline";
  return (
    <span className={className}>
      waiting on{" "}
      <Link
        to={decisionsPageHref(factoryId, { decision: first })}
        componentId={componentId}
        className={link}
      >
        {first}
      </Link>
      {more > 0 && (
        <>
          {" "}
          <Link
            to={decisionsPageHref(factoryId)}
            componentId={componentId}
            className={link}
            title={`${more} more open question${more === 1 ? "" : "s"}`}
          >
            +{more}
          </Link>
        </>
      )}
    </span>
  );
}

/** Each ref linked to its decision, comma separated (the loop strip, the task inspector). */
export function HeldRefList({
  held,
  factoryId,
  componentId,
  className,
}: {
  held: LoopHoldView;
  factoryId: string;
  componentId: string;
  className?: string;
}) {
  if (!heldOnAnswer(held)) return <HeldLabel held={held} className={className} />;
  const more = heldTotal(held) - held.refs.length;
  return (
    <span className={cn("font-mono", className)}>
      {held.refs.map((ref, i) => (
        <span key={ref}>
          {i > 0 && ", "}
          <Link
            to={decisionsPageHref(factoryId, { decision: ref })}
            componentId={componentId}
            className="whitespace-nowrap underline-offset-2 hover:underline"
          >
            {ref}
          </Link>
        </span>
      ))}
      {more > 0 && (
        <>
          {" "}
          <Link
            to={decisionsPageHref(factoryId)}
            componentId={componentId}
            className="whitespace-nowrap underline-offset-2 hover:underline"
          >
            +{more}
          </Link>
        </>
      )}
    </span>
  );
}
