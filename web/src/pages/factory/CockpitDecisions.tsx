/**
 * Decisions (`/decisions`): everything the factory asks this person, ranked
 * by what each one unblocks, in the product first and the factory's own
 * asks one click away.
 */

import { useState } from "react";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { NodeContextProvider } from "@/components/factory/v3/NodeContext";
import { useAnswerAsk, useNeedsYou } from "@/components/factory/v3/nodeSteer";
import { useNodeSource } from "@/components/factory/v3/nodeSource";
import { NeedsList, useSheetRows } from "@/components/factory/v3/sheets/V3Sheets";

export function CockpitDecisions() {
  const { factoryId: fid } = useFactoryShell();
  const [tree, setTree] = useState("product");
  const query = useNeedsYou(tree, true, fid);
  const source = useNodeSource();
  const onAnswer = useAnswerAsk(null, { sample: source?.kind === "sample", factoryId: fid });
  const list = query.data;
  const rows = useSheetRows(tree, list, true, fid);
  return (
    <div
      data-testid="cockpit-decisions-page"
      className="mx-auto flex max-w-[56rem] min-w-0 flex-col gap-4"
    >
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-xl font-semibold tracking-tight">Decisions</h1>
        <span className="text-sm text-muted-foreground">
          {tree === "factory" ? "How the factory runs" : "What you are building"} · ranked by what
          each one unblocks
        </span>
      </header>
      {list && rows ? (
        <NodeContextProvider value={{ factoryId: fid, onAnswer }}>
          <NeedsList rows={rows} list={list} tree={tree} onTree={setTree} />
        </NodeContextProvider>
      ) : query.isError ? (
        <p role="alert" className="text-sm text-status-red-fg">
          The decisions could not be read.{" "}
          <button type="button" onClick={() => void query.refetch()} className="underline">
            Try again
          </button>
        </p>
      ) : (
        <div aria-busy="true" className="h-48 animate-pulse rounded-xl bg-muted" />
      )}
    </div>
  );
}
