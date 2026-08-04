"use client";

/**
 * Phase 19C — interactive graph engine.
 *
 * A production graph engine built on @xyflow/react (React Flow v12):
 * pan / zoom / drag / fit-view / minimap / controls / fullscreen all come
 * from the library — nothing custom. d3-force only computes the initial
 * layout positions (a layout *algorithm*, not a graph engine).
 *
 * Incremental expansion: existing node positions are cached and reseeded into
 * the force simulation when new nodes arrive, so the layout evolves smoothly.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  ControlButton,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import {
  computeForceLayout,
  hexFor,
  nodeTypeLabel,
  relHex,
  relLabel,
  truncate,
  type LayoutPoint,
  type VizEdge,
  type VizNode,
} from "@/lib/graph/graphModel";

// ── Custom node view ───────────────────────────────────────────

interface GraphNodeData {
  label: string;
  nodeType: string;
  sublabel?: string;
  repositoryId?: string;
  root?: boolean;
  dimmed?: boolean;
  highlighted?: boolean;
  [key: string]: unknown;
}

type GraphFlowNode = Node<GraphNodeData, "graphNode">;

function GraphNodeView({ data, selected }: NodeProps<GraphFlowNode>) {
  const { label, nodeType, sublabel, repositoryId, root, dimmed, highlighted } =
    data;
  const color = hexFor(nodeType);
  const emphasis = selected || highlighted;

  return (
    <div
      className={[
        "graph-node rounded-lg border-2 px-2.5 py-1.5 min-w-[120px] max-w-[220px]",
        "bg-white/95 dark:bg-slate-800/95 shadow-sm",
        emphasis ? "border-indigo-500 dark:border-indigo-400" : "border-slate-300 dark:border-slate-600",
        dimmed ? "opacity-25" : "opacity-100",
        root ? "ring-2 ring-indigo-400/60" : "",
      ].join(" ")}
    >
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <div className="flex items-center gap-1.5">
        <span
          className="w-2.5 h-2.5 rounded-full shrink-0"
          style={{ backgroundColor: color }}
        />
        <span
          className="text-[11px] font-semibold leading-tight text-slate-800 dark:text-slate-100 truncate"
          title={label}
        >
          {truncate(label, 34)}
        </span>
      </div>
      <div className="mt-0.5 flex items-center gap-1.5 text-[9px] font-medium uppercase tracking-wide text-slate-400">
        <span>{nodeTypeLabel(nodeType)}</span>
        {repositoryId && repositoryId !== "default" && (
          <span className="font-mono normal-case">· {repositoryId}</span>
        )}
      </div>
      {sublabel && (
        <div className="mt-0.5 text-[9px] font-mono text-slate-400 truncate" title={sublabel}>
          {truncate(sublabel, 40)}
        </div>
      )}
    </div>
  );
}

const nodeTypes = { graphNode: GraphNodeView };

// ── Engine props ───────────────────────────────────────────────

export interface InteractiveGraphProps {
  nodes: VizNode[];
  edges: VizEdge[];
  selectedId: string | null;
  /** Node ids to emphasize (e.g. selected node's neighbors). */
  highlightedIds?: Set<string> | null;
  /** Root id (breadcrumb start). */
  rootId?: string | null;
  /** Node to center on; changes trigger a smooth fit. */
  focusId?: string | null;
  /** Bump to re-run the force layout from scratch. */
  resetToken?: number;
  /** Bump to fit the whole graph into view. */
  fitToken?: number;
  heightClass?: string;
  onSelectNode: (id: string | null) => void;
  onExpandNode: (id: string) => void;
  onRelayout?: () => void;
}

function InteractiveGraphInner({
  nodes,
  edges,
  selectedId,
  highlightedIds,
  rootId,
  focusId,
  resetToken = 0,
  fitToken = 0,
  heightClass = "h-[640px]",
  onSelectNode,
  onExpandNode,
  onRelayout,
}: InteractiveGraphProps) {
  const { fitView } = useReactFlow();
  const containerRef = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);

  const graphSignature = useMemo(
    () =>
      `${nodes
        .map((n) => n.id)
        .join("|")}::${edges
        .map((e) => e.id)
        .join("|")}`,
    [nodes, edges]
  );

  // Cached layout — reseeded on incremental expansion, reset on demand.
  const layoutCache = useRef<Map<string, LayoutPoint>>(new Map());
  const prevReset = useRef(resetToken);

  const layout = useMemo(() => {
    if (!nodes.length) return new Map<string, LayoutPoint>();
    if (prevReset.current !== resetToken) {
      layoutCache.current.clear();
      prevReset.current = resetToken;
    }
    const cached = layoutCache.current;
    const initial: Record<string, { x: number; y: number }> = {};
    for (const n of nodes) {
      const p = cached.get(n.id);
      if (p) initial[n.id] = { x: p.x, y: p.y };
    }
    const points = computeForceLayout(nodes, edges, {
      width: 1600,
      height: 1000,
      iterations: 220,
      linkDistance: 150,
      charge: -340,
      initialPositions: initial,
    });
    const next = new Map<string, LayoutPoint>();
    for (const p of points) next.set(p.id, p);
    layoutCache.current = next;
    return next;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphSignature, resetToken]);

  const rfNodes: GraphFlowNode[] = useMemo(
    () =>
      nodes.map((n) => {
        const pos = layout.get(n.id) ?? { x: 0, y: 0 };
        return {
          id: n.id,
          type: "graphNode",
          position: pos,
          data: {
            label: n.label,
            nodeType: n.nodeType,
            sublabel: n.sublabel,
            repositoryId: n.repositoryId,
            root: rootId != null && rootId === n.id,
            dimmed: highlightedIds != null && !highlightedIds.has(n.id) && highlightedIds.size > 0,
            highlighted: highlightedIds?.has(n.id) ?? false,
          },
          selected: selectedId === n.id,
        };
      }),
    [nodes, layout, selectedId, highlightedIds, rootId]
  );

  const selectedEdgeIds = useMemo(() => {
    if (!selectedId) return new Set<string>();
    const set = new Set<string>();
    for (const e of edges) {
      if (e.source === selectedId || e.target === selectedId) set.add(e.id);
    }
    return set;
  }, [edges, selectedId]);

  const rfEdges: Edge[] = useMemo(
    () =>
      edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: "default",
        animated: selectedEdgeIds.has(e.id),
        label: selectedEdgeIds.has(e.id) ? relLabel(e.relationship) : undefined,
        style: {
          stroke: relHex(e.relationship),
          strokeWidth: e.virtual ? 1 : 1.2 + Math.min(e.weight, 1) * 2.4,
          strokeDasharray: e.virtual ? "6 5" : undefined,
          opacity: selectedId && !selectedEdgeIds.has(e.id) ? 0.25 : 0.85,
        },
      })),
    [edges, selectedId, selectedEdgeIds]
  );

  // Fit the whole graph on mount and whenever fitToken bumps.
  useEffect(() => {
    const t = setTimeout(() => {
      fitView({ padding: 0.18, maxZoom: 1.2, duration: 500 });
    }, 40);
    return () => clearTimeout(t);
  }, [fitToken, fitView]);

  // Center a specific node (cross-repo jumps, breadcrumb navigation).
  const focusNode = useMemo(
    () => (focusId ? rfNodes.find((n) => n.id === focusId) : undefined),
    [focusId, rfNodes]
  );

  useEffect(() => {
    if (!focusNode) return;
    const t = setTimeout(() => {
      fitView({ nodes: [focusNode], padding: 0.5, maxZoom: 1.6, duration: 600 });
    }, 40);
    return () => clearTimeout(t);
  }, [focusNode, fitView]);

  const toggleFullscreen = () => {
    const el = containerRef.current;
    if (!el) return;
    if (!document.fullscreenElement) {
      void el.requestFullscreen().catch(() => undefined);
    } else {
      void document.exitFullscreen();
    }
  };

  useEffect(() => {
    const onFsChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);

  const largeGraph = nodes.length > 200;

  return (
    <div
      ref={containerRef}
      className={`relative ${fullscreen ? "" : heightClass} rounded-xl overflow-hidden border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/60 touch-none`}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        minZoom={0.15}
        maxZoom={2.5}
        fitView
        fitViewOptions={{ padding: 0.18, maxZoom: 1.2 }}
        onlyRenderVisibleElements={largeGraph}
        elevateEdgesOnSelect
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, node) => onSelectNode(node.id)}
        onNodeDoubleClick={(_, node) => onExpandNode(node.id)}
        onPaneClick={() => onSelectNode(null)}
        defaultEdgeOptions={{ type: "default" }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={22}
          size={1.2}
          color="#94a3b8"
        />
        <MiniMap
          pannable
          zoomable
          nodeColor={(n) => hexFor((n.data as GraphNodeData | undefined)?.nodeType ?? "run")}
          nodeStrokeWidth={2}
          maskColor="rgba(15, 23, 42, 0.25)"
        />
        <Controls position="bottom-left" showInteractive={false}>
          <ControlButton
            title="Relayout (re-run force simulation)"
            onClick={() => onRelayout?.()}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
            </svg>
          </ControlButton>
          <ControlButton title="Toggle fullscreen" onClick={toggleFullscreen}>
            {fullscreen ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15" />
              </svg>
            )}
          </ControlButton>
        </Controls>
      </ReactFlow>

      <div className="absolute top-2 right-2 flex items-center gap-1.5 pointer-events-none">
        <span className="text-[10px] font-mono text-slate-400 bg-white/70 dark:bg-slate-800/70 rounded px-2 py-1">
          {nodes.length} nodes · {edges.length} edges
        </span>
        {largeGraph && (
          <span className="text-[10px] font-mono text-amber-600 dark:text-amber-400 bg-white/70 dark:bg-slate-800/70 rounded px-2 py-1">
            virtualized
          </span>
        )}
      </div>
      <div className="absolute bottom-2 left-1/2 -translate-x-1/2 text-[10px] font-mono text-slate-400 bg-white/70 dark:bg-slate-800/70 rounded px-2 py-1 pointer-events-none">
        click node = inspect · double-click = expand neighbors · drag = move ·
        wheel = zoom
      </div>
    </div>
  );
}

export function InteractiveGraph(props: InteractiveGraphProps) {
  return (
    <ReactFlowProvider>
      <InteractiveGraphInner {...props} />
    </ReactFlowProvider>
  );
}
