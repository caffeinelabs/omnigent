/**
 * A pannable, zoomable React Flow canvas for one factory graph, as the
 * sub-agent graph draws it (same zoom controls and background), with edge
 * tones mapped onto the app's tokens.
 */

import { type MouseEvent, useEffect, useMemo, useRef } from "react";
import type { Edge, Node } from "@xyflow/react";
import {
  Background,
  ReactFlow,
  ReactFlowProvider,
  getViewportForBounds,
  useReactFlow,
  useStore,
} from "@xyflow/react";
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

const FIT_PADDING = 0.12;
const EDGE_GAP_PX = 16;

/**
 * The first view of a node set: fit, but never below `minZoom`, so a big
 * graph opens readable, to pan. One that still overflows the canvas starts
 * at its top-left corner (lane titles, the first wave) rather than centred.
 */
function InitialView({ minZoom, fitKey }: { minZoom: number; fitKey: string }) {
  const { getNodes, getNodesBounds, setViewport } = useReactFlow();
  // Controlled nodes never get `nodesInitialized`; the lookup's sizes tell.
  const measured = useStore((s) => {
    if (s.nodeLookup.size === 0) return false;
    for (const node of s.nodeLookup.values()) {
      if (!node.measured.width || !node.measured.height) return false;
    }
    return true;
  });
  const width = useStore((s) => s.width);
  const height = useStore((s) => s.height);
  const maxZoom = useStore((s) => s.maxZoom);
  const fitted = useRef<string | null>(null);
  useEffect(() => {
    if (!measured || !width || !height || fitted.current === fitKey) return;
    const nodes = getNodes();
    if (nodes.length === 0) return;
    fitted.current = fitKey;
    // The instance's bounds use the measured sizes.
    const bounds = getNodesBounds(nodes);
    const view = getViewportForBounds(bounds, width, height, minZoom, maxZoom, FIT_PADDING);
    const wide = bounds.width * view.zoom + 2 * EDGE_GAP_PX > width;
    const tall = bounds.height * view.zoom + 2 * EDGE_GAP_PX > height;
    void setViewport({
      x: wide ? EDGE_GAP_PX - bounds.x * view.zoom : view.x,
      y: tall ? EDGE_GAP_PX - bounds.y * view.zoom : view.y,
      zoom: view.zoom,
    });
  }, [measured, width, height, maxZoom, minZoom, fitKey, getNodes, getNodesBounds, setViewport]);
  return null;
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
  // A new node set (scope, hidden done tasks) gets a fresh first view.
  const fitKey = useMemo(() => nodes.map((n) => n.id).join(","), [nodes]);
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
          <InitialView minZoom={fitMinZoom} fitKey={fitKey} />
          <Background bgColor="var(--card)" />
          <ZoomControls />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}
