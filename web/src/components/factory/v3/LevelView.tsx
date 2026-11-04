// One level under a node, grouped under each item's parent. Parents are
// section titles only (never nodes, never joined by lines); groups whose work
// is all done fold into one line, and rows page at 60 (a group's next 60 come
// from the server when it has more).

import { useState } from "react";
import { cn } from "@/lib/utils";
import { fullTitle, nodeName } from "./calm/calmModel";
import { DoneLine, MoreFooter, PagedRows } from "./Children";
import { NodeLink, StateDot } from "./nodeParts";
import { useMoreInGroup } from "./nodeQueries";
import { NodeTile, type Overlays, ParentNameContext } from "./NodeTile";
import type { LevelGroup, LevelView as View } from "./nodeTypes";
import { drawsTiles } from "./nodeWire";

/** A group's head: its parent's short name, a link (the parent's level and lineage are Details there). */
function GroupHead({ group }: { group: LevelGroup }) {
  const p = group.parent;
  const name = nodeName(p);
  return (
    <div className="mb-1.5 px-0.5 leading-normal" data-testid="v3-group-head">
      <NodeLink
        nodeRef={p.ref}
        title={fullTitle(p) ?? undefined}
        className={cn(
          "text-[13px] font-semibold text-muted-foreground hover:text-status-blue-fg",
          p.implicit && "italic",
        )}
      >
        {name}
      </NodeLink>
    </div>
  );
}

function Group({
  view,
  group: first,
  rows,
  overlays,
}: {
  view: View;
  group: LevelGroup;
  rows: boolean;
  overlays: Overlays;
}) {
  const more = useMoreInGroup(view.scope, view.level, first);
  const group = more.items.length ? { ...first, items: [...first.items, ...more.items] } : first;
  const live = group.items.filter((k) => k.state !== "done");
  const done = group.items.filter((k) => k.state === "done");
  return (
    <div className="mb-[18px]" data-testid="v3-group" data-ref={group.parent.ref}>
      <GroupHead group={group} />
      {group.folded_done ? (
        <DoneLine done={group.items} rows={rows} />
      ) : rows ? (
        <div className="flex flex-col gap-1.5">
          {(live.length > 0 || done.length === 1) && (
            <PagedRows items={done.length === 1 ? group.items : live} />
          )}
          {done.length > 1 && <DoneLine done={done} rows />}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {live.length > 0 && (
            <ul
              role="list"
              className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,240px),1fr))] gap-3"
            >
              {live.map((k) => (
                <NodeTile key={k.ref} node={k} overlays={overlays} />
              ))}
            </ul>
          )}
          <DoneLine done={done} />
        </div>
      )}
      <MoreFooter more={more} shown={group.items.length} />
      {group.later?.length ? (
        <div
          className="mt-1.5 flex items-center gap-2 px-1 text-[13px] text-muted-foreground"
          title={group.later.join(", ")}
        >
          <StateDot state="later" />
          {group.later.length} later
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
  const rows = lens === "list" || !drawsTiles(view);
  const fold = Boolean(view.done_groups) && !showDone;
  const groups = fold ? view.groups.filter((g) => !g.folded_done) : view.groups;
  return (
    <div data-testid="v3-level-view">
      {groups.map((g) => {
        // A child named after its parent keeps only the rest ("Part 1").
        const parentName = nodeName(g.parent);
        return (
          <ParentNameContext.Provider key={g.parent.ref} value={parentName}>
            <Group view={view} group={g} rows={rows} overlays={overlays} />
          </ParentNameContext.Provider>
        );
      })}
      {fold && view.done_groups && (
        <button
          type="button"
          data-testid="v3-done-groups"
          onClick={() => setShowDone(true)}
          title={view.done_groups.titles.join(" · ")}
          className="flex items-center gap-2 rounded-[8px] px-1 py-1 text-left text-[13px] text-muted-foreground hover:text-foreground"
        >
          <span aria-hidden className="font-bold text-status-green-fg">
            ✓
          </span>
          <span>
            {view.done_groups.count} {view.done_groups.noun ?? "all done"}
          </span>
        </button>
      )}
    </div>
  );
}
