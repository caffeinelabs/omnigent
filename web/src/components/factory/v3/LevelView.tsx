// One level under a node, grouped under each item's parent. Parents are
// section titles only (never nodes, never joined by lines); groups whose work
// is all done fold into one line, and rows page at 60.

import { useState } from "react";
import { DoneLine, PagedRows } from "./Children";
import { NodeLink, StateDot } from "./nodeParts";
import { NodeTile, type Overlays } from "./NodeTile";
import type { LevelGroup, LevelView as View } from "./nodeTypes";

function GroupHead({ group }: { group: LevelGroup }) {
  const p = group.parent;
  return (
    <div className="mb-2 leading-normal" data-testid="v3-group-head">
      <NodeLink
        nodeRef={p.ref}
        className="mr-2 text-[13.5px] font-semibold hover:text-status-blue-fg"
      >
        {p.title}
      </NodeLink>
      {p.tag && <span className="mr-2 font-mono text-xs text-foreground-tertiary">{p.tag}</span>}
      {p.implicit && (
        <span className="mr-2 rounded-full border border-dashed border-border-strong px-1.5 text-[11.5px] text-foreground-tertiary">
          implicit
        </span>
      )}
      <span className="mr-2 text-xs text-foreground-tertiary">
        {p.level_name}
        {p.ancestors.length ? ` · in ${p.ancestors.join(" › ")}` : ""}
      </span>
      {p.evidence && <span className="text-[12.5px] text-muted-foreground">· {p.evidence}</span>}
    </div>
  );
}

function Group({
  group,
  rows,
  overlays,
}: {
  group: LevelGroup;
  rows: boolean;
  overlays: Overlays;
}) {
  const live = group.items.filter((k) => k.state !== "done");
  const done = group.items.filter((k) => k.state === "done");
  return (
    <div className="mb-[18px]" data-testid="v3-group" data-ref={group.parent.ref}>
      <GroupHead group={group} />
      {group.folded_done ? (
        <DoneLine done={group.items} overlays={overlays} rows={rows} />
      ) : rows ? (
        <PagedRows items={group.items} overlays={overlays} owner />
      ) : (
        <div className="flex flex-col gap-3">
          {live.length > 0 && (
            <ul
              role="list"
              className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,290px),1fr))] gap-3"
            >
              {live.map((k) => (
                <NodeTile key={k.ref} node={k} overlays={overlays} />
              ))}
            </ul>
          )}
          <DoneLine done={done} overlays={overlays} />
        </div>
      )}
      {group.later?.length ? (
        <div className="mt-2 flex items-center gap-2 rounded-[10px] border border-dashed border-border-strong px-3 py-2 text-[13px] text-muted-foreground">
          <StateDot state="later" />
          {group.later.length} later: {group.later.join(", ")}
        </div>
      ) : null}
    </div>
  );
}

export function LevelView({
  view,
  lens,
  overlays,
}: {
  view: View;
  lens: "map" | "list";
  overlays: Overlays;
}) {
  const [showDone, setShowDone] = useState(false);
  const rows = lens === "list" || !view.tiles;
  const fold = Boolean(view.done_groups) && !showDone;
  const groups = fold ? view.groups.filter((g) => !g.folded_done) : view.groups;
  return (
    <div data-testid="v3-level-view">
      {groups.map((g) => (
        <Group key={g.parent.ref} group={g} rows={rows} overlays={overlays} />
      ))}
      {fold && view.done_groups && (
        <button
          type="button"
          data-testid="v3-done-groups"
          onClick={() => setShowDone(true)}
          className="flex w-full flex-wrap items-center gap-2 rounded-[10px] border border-dashed border-border-strong px-3 py-2 text-left text-[13px] text-muted-foreground hover:text-foreground"
        >
          <span aria-hidden className="font-bold text-status-green-fg">
            ✓
          </span>
          <b className="font-semibold text-foreground">
            {view.done_groups.count} {view.done_groups.noun}
          </b>
          <span>
            {view.done_groups.titles.slice(0, 5).join(" · ")}
            {view.done_groups.titles.length > 5 ? ` +${view.done_groups.titles.length - 5}` : ""}
          </span>
          {view.done_groups.in_base_env && (
            <span className="text-foreground-tertiary">· in base env</span>
          )}
          <span className="flex-1" />
          <span className="text-foreground-tertiary">show</span>
        </button>
      )}
    </div>
  );
}
