// A node's own content above its children, when it has some: a feature's
// acceptance steps (what passes on the base env) and a need's evidence (the
// ask verbatim, what is missing). Neither renders when the node has none.

import { cn } from "@/lib/utils";
import type { NodeFull } from "./nodeTypes";

const BLOCK = "rounded-[10px] bg-card-solid px-3.5 py-2.5 ring-1 ring-border";

function Acceptance({ acceptance }: { acceptance: NonNullable<NodeFull["acceptance"]> }) {
  const steps = acceptance.steps;
  const pass = steps.filter((s) => s.result === "pass").length;
  const fail = steps.filter((s) => s.result === "fail").length;
  const notRun = steps.length - pass - fail;
  const words = [
    `${pass} of ${steps.length} pass on base env`,
    fail ? `${fail} fails` : null,
    notRun ? `${notRun} not run yet` : null,
  ];
  return (
    <section className={BLOCK} aria-label="Acceptance" data-testid="v3-acceptance">
      <div className="mb-1.5 flex flex-wrap items-center gap-2.5 text-[13px]">
        <b className="font-semibold">Acceptance</b>
        <span className="text-muted-foreground">{words.filter(Boolean).join(" · ")}</span>
        {acceptance.note && (
          <span className="text-xs text-foreground-tertiary">{acceptance.note}</span>
        )}
      </div>
      <ol className="grid grid-cols-1 gap-x-[18px] gap-y-[3px] lg:grid-cols-2">
        {steps.map((s) => (
          <li
            key={s.text}
            className="flex min-w-0 items-baseline gap-2 text-[12.8px] text-muted-foreground"
          >
            <span
              aria-label={
                s.result === "pass" ? "passes" : s.result === "fail" ? "fails" : "not run"
              }
              className={cn(
                "w-3.5 shrink-0 text-center font-bold",
                s.result === "pass" && "text-status-green-fg",
                s.result === "fail" && "text-status-red-fg",
                s.result === "none" && "text-foreground-tertiary",
              )}
            >
              {s.result === "pass" ? "✓" : s.result === "fail" ? "✗" : "○"}
            </span>
            <span className={cn(s.result !== "none" && "font-semibold text-foreground")}>
              {s.text}{" "}
              <span className="text-[11.5px] font-normal text-foreground-tertiary">
                · {s.source}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Evidence({ evidence }: { evidence: NonNullable<NodeFull["evidence"]> }) {
  return (
    <section className={BLOCK} aria-label="Evidence" data-testid="v3-evidence">
      <div className="mb-1.5 flex flex-wrap items-center gap-2.5 text-[13px]">
        <b className="font-semibold">Evidence</b>
        <span className="text-muted-foreground">{evidence.summary}</span>
      </div>
      {evidence.items.map((e) => (
        <div
          key={`${e.kind}:${e.text}`}
          className="grid grid-cols-[74px_minmax(0,1fr)] gap-x-2.5 py-[3px] text-[12.8px] text-muted-foreground"
        >
          <span className="text-foreground-tertiary">{e.kind}</span>
          <span>
            <span className={cn(e.quote && "text-foreground italic")}>{e.text}</span>
            {e.who && <span className="text-foreground-tertiary"> · {e.who}</span>}
          </span>
        </div>
      ))}
    </section>
  );
}

export function NodeContent({ node }: { node: NodeFull }) {
  if (!node.acceptance?.steps.length && !node.evidence?.items.length) return null;
  return (
    <div className="flex flex-col gap-3">
      {node.acceptance?.steps.length ? <Acceptance acceptance={node.acceptance} /> : null}
      {node.evidence?.items.length ? <Evidence evidence={node.evidence} /> : null}
    </div>
  );
}
