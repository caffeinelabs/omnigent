/**
 * A pannable, zoomable React Flow canvas for one factory graph, as the
 * sub-agent graph draws it (same zoom controls and background), with edge
 * tones mapped onto the app's tokens.
 */

import { type MouseEvent, useMemo } from "react";
import type { Edge, Node } from "@xyflow/react";
import { Background, ReactFlow, ReactFlowProvider } from "@xyflow/react";
import { ZoomControls } from "@/shell/SubagentsGraphView";
import { cn } from "@/lib/utils";
import { GRAPH_NODE_TYPES } from "./GraphNodes";

import "@xyflow/react/dist/style.css";

const STROKE: Record<string, string> = {
  "factory-edge-critical": "var(--foreground)",
  "factory-edge-met": "color-mix(in oklab, var(--muted-foreground) 45%, transparent)",
  "factory-edge-open": "var(--muted-foreground)",
  "factory-edge-project": "var(--brand-accent, var(--primary))",
  "factory-edge-message": "color-mix(in oklab, var(--session-active) 70%, var(--muted-foreground))",
  "factory-edge-human": "var(--destructive)",
  "factory-edge-warn": "var(--warning)",
  "factory-edge-structure": "color-mix(in oklab, var(--muted-foreground) 40%, transparent)",
};

/** Edges with their tone as a stroke and labels on the card colour. */
export function toneEdges(edges: readonly Edge[]): Edge[] {
  return edges.map((edge) => ({
    ...edge,
    style: { stroke: STROKE[edge.className ?? ""] ?? "var(--muted-foreground)", ...edge.style },
    labelStyle: { fill: "var(--muted-foreground)", fontSize: 11, fontFamily: "var(--font-mono)" },
    labelBgStyle: { fill: "var(--card-solid, var(--card))", fillOpacity: 0.92 },
    labelBgPadding: [4, 2] as [number, number],
    labelBgBorderRadius: 4,
  }));
}

export function GraphCanvas({
  nodes,
  edges,
  onNodeClick,
  testId,
  className,
  minZoom = 0.15,
  fitMinZoom = 0.55,
}: {
  nodes: Node[];
  edges: Edge[];
  onNodeClick?: (event: MouseEvent, node: Node) => void;
  testId: string;
  className?: string;
  minZoom?: number;
  /** The smallest zoom the first fit uses. */
  fitMinZoom?: number;
}) {
  const toned = useMemo(() => toneEdges(edges), [edges]);
  return (
    <div
      data-testid={testId}
      data-nodes={nodes.length}
      data-edges={edges.length}
      className={cn(
        "relative h-full min-h-0 w-full overflow-hidden rounded-xl border border-border bg-card",
        className,
      )}
    >
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={toned}
          nodeTypes={GRAPH_NODE_TYPES}
          onNodeClick={onNodeClick}
          fitView
          // Readable first: a big graph opens zoomed in, to pan, not as a thumbnail.
          fitViewOptions={{ padding: 0.12, minZoom: fitMinZoom }}
          panOnDrag
          panOnScroll
          zoomOnDoubleClick={false}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          proOptions={{ hideAttribution: true }}
          minZoom={minZoom}
          maxZoom={2.5}
        >
          <Background bgColor="var(--card)" />
          <ZoomControls />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}
