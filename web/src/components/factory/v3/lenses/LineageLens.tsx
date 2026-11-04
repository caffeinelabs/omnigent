// The Lineage lens: where a node came from, read top to bottom. Each ancestor
// with its one line (a need with its evidence, a feature with what approved
// it), the node itself, then what is below it as counts per level (each opens
// that level) and where it landed (merged, in the base env, readouts). It
// never draws a tree of descendants, and nothing joins the rows.

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { LevelCountWords, heldLevels, readoutWords, resultsWords } from "../LevelStrip";
import { NodeLink } from "../nodeParts";
import type { LineageLink, LineageRow, LineageView } from "../nodeTypes";
import { levelName } from "../nodeWire";
import { RichText } from "../RichText";
import { StatusWords } from "../StatusLine";

const KEY =
  "pt-0.5 text-[10.5px] font-medium tracking-[0.07em] uppercase text-foreground-tertiary [overflow-wrap:anywhere]";
const SUB = "text-[12.8px] leading-[1.5] text-muted-foreground";

function linkWords(link: LineageLink): ReactNode {
  return (
    <span>
      {link.kind}{" "}
      {link.ref ? (
        <NodeLink nodeRef={link.ref} className="text-foreground hover:text-status-blue-fg">
          {link.ref}
        </NodeLink>
      ) : null}
      {link.title && link.title !== link.ref ? ` · ${link.title}` : ""}
      {link.state ? ` · ${link.state}` : ""}
    </span>
  );
}

/** What a row says under its title: its evidence or approval when it has one, else its line. */
function RowWords({ row, here }: { row: LineageRow; here?: boolean }) {
  const extra: { key: string; part: ReactNode }[] = [
    ...(row.evidence ? [{ key: "evidence", part: row.evidence }] : []),
    ...(row.links ?? []).map((link) => ({
      key: `${link.kind}:${link.ref ?? link.title}`,
      part: linkWords(link),
    })),
  ];
  const parts =
    here && row.progress?.what === "acceptance"
      ? [{ key: "progress", part: `acceptance ${row.progress.label} on base env` }, ...extra]
      : extra;
  return (
    <>
      {row.why_html && (
        <span className={SUB}>
          <RichText html={row.why_html} />
        </span>
      )}
      {parts.length > 0 && (
        <span className={SUB}>
          {parts.map(({ key, part }, i) => (
            <span key={key}>
              {i > 0 && " · "}
              {part}
            </span>
          ))}
        </span>
      )}
      {(here || !extra.length) && (
        <StatusWords line={row.line} nodeRef={row.ref} compact className={SUB} />
      )}
    </>
  );
}

function Row({
  label,
  children,
  here,
  testId,
}: {
  label: string;
  children: ReactNode;
  here?: boolean;
  testId?: string;
}) {
  return (
    <li
      data-testid={testId}
      className={cn(
        "grid grid-cols-[minmax(0,1fr)] gap-x-4 gap-y-0.5 border-t border-border px-3.5 py-2.5 first:border-t-0 sm:grid-cols-[112px_minmax(0,1fr)]",
        here && "bg-status-blue/10",
      )}
    >
      <span className={KEY}>{label}</span>
      <span className="flex min-w-0 flex-col gap-0.5">{children}</span>
    </li>
  );
}

function rowTitle(row: LineageRow): string {
  return row.tag && row.tag !== row.title ? `${row.tag} · ${row.title}` : row.title;
}

export function LineageLens({ view }: { view: LineageView }) {
  const below = heldLevels(view.below);
  const results = view.results;
  return (
    <ol
      data-testid="v3-lineage"
      aria-label="Lineage"
      className="overflow-hidden rounded-xl bg-card-solid ring-1 ring-border"
    >
      {view.ancestors.map((row) => (
        <Row key={row.ref} label={levelName(row.level, row.level_name)} testId="v3-lineage-row">
          <NodeLink
            nodeRef={row.ref}
            roving
            className="text-[14px] font-semibold text-foreground [overflow-wrap:anywhere] hover:text-status-blue-fg"
          >
            {rowTitle(row)}
          </NodeLink>
          <RowWords row={row} />
        </Row>
      ))}
      <Row
        label={`${levelName(view.here.level, view.here.level_name)} · here`}
        here
        testId="v3-lineage-here"
      >
        <span aria-current="page" className="text-[14px] font-semibold [overflow-wrap:anywhere]">
          {rowTitle(view.here)}
        </span>
        <RowWords row={view.here} here />
      </Row>
      {below.length > 0 && (
        <Row label="Below" testId="v3-lineage-below">
          <span className="flex flex-wrap items-center gap-1.5">
            {below.map((c, i) => (
              <span key={c.level} className="inline-flex items-center gap-1.5">
                {i > 0 && (
                  <span aria-hidden className="text-foreground-tertiary">
                    ›
                  </span>
                )}
                <NodeLink
                  nodeRef={view.scope}
                  level={c.level}
                  title={`All ${c.name.toLowerCase()} under this node, grouped under their parents`}
                  className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-px text-[12.5px] text-muted-foreground hover:border-status-blue hover:text-foreground"
                >
                  <span>{c.name}</span>
                  <LevelCountWords c={c} />
                </NodeLink>
              </span>
            ))}
          </span>
        </Row>
      )}
      {results && (
        <Row label="Results" testId="v3-lineage-results">
          <span className="flex flex-wrap items-center gap-x-1.5 text-[13px]">
            <span>{resultsWords(results)}</span>
            <span aria-hidden className="text-foreground-tertiary">
              →
            </span>
            <span className="text-foreground-tertiary">{readoutWords(results)}</span>
          </span>
          {results.open.length > 0 && (
            <span className={SUB}>
              {results.open.length} open: {results.open.join(", ")}
            </span>
          )}
        </Row>
      )}
    </ol>
  );
}
