"use client";

import { useEffect, useMemo, useRef, useState } from "react";

// ── Node visuals ───────────────────────────────────────────────

const NODE_HEX: Record<string, string> = {
  repository: "#4f46e5",
  folder: "#818cf8",
  file: "#0ea5e9",
  module: "#38bdf8",
  package: "#06b6d4",
  class: "#3b82f6",
  interface: "#8b5cf6",
  function: "#10b981",
  method: "#34d399",
  requirement: "#f59e0b",
  acceptance_criterion: "#fbbf24",
  implementation_plan: "#f97316",
  plan_version: "#fb923c",
  goal: "#d946ef",
  patch: "#f43f5e",
  commit_candidate: "#fb7185",
  test: "#22c55e",
  test_suite: "#4ade80",
  review_finding: "#eab308",
  quality_gate: "#a855f7",
  evidence: "#14b8a6",
  consensus: "#2dd4bf",
  contradiction: "#ef4444",
  notebook_entry: "#0891b2",
  decision: "#ec4899",
  run: "#64748b",
  agent: "#6b7280",
  repository_memory: "#84cc16",
};

export function hexFor(nodeType: string): string {
  return NODE_HEX[nodeType] || "#94a3b8";
}

export function nodeTypeLabel(nodeType: string): string {
  return nodeType.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function truncate(s: string, n = 90): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

// ── Graph data model ───────────────────────────────────────────

export interface VizNode {
  id: string;
  label: string;
  nodeType: string;
  repositoryId?: string;
  sublabel?: string;
  data?: { [k: string]: unknown };
}

export interface VizEdge {
  id: string;
  source: string;
  target: string;
  relationship: string;
  weight: number;
  virtual?: boolean;
}

// ── Force-directed SVG canvas ──────────────────────────────────

export interface ForceGraphProps {
  nodes: VizNode[];
  edges: VizEdge[];
  selectedId: string | null;
  hoveredId: string | null;
  resetToken: number;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
}

interface SimNode extends VizNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  mass: number;
  fixed: boolean;
}

interface SimEdge {
  s: number;
  t: number;
  rest: number;
  weight: number;
  virtual?: boolean;
  relationship: string;
}

interface DragState {
  kind: "pan" | "node";
  pointerId: number;
  startX: number;
  startY: number;
  originX: number;
  originY: number;
  moved: number;
  nodeId?: string;
}

export function ForceGraph({
  nodes,
  edges,
  selectedId,
  hoveredId,
  resetToken,
  onSelect,
  onHover,
}: ForceGraphProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [, setFrame] = useState(0);

  const simRef = useRef<{
    nodes: SimNode[];
    edges: SimEdge[];
    index: Map<string, number>;
    alpha: number;
    stopped: boolean;
    raf: number;
  } | null>(null);
  const dragRef = useRef<DragState | null>(null);

  // Graph identity — rebuild the simulation when the topology changes.
  const graphKey = useMemo(
    () => `${nodes.map((n) => n.id).join("|")}::${edges.map((e) => e.id).join("|")}`,
    [nodes, edges]
  );

  // Container sizing.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) setSize({ w: r.width, h: r.height });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Non-passive wheel zoom.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((z) => Math.min(3, Math.max(0.4, z * (e.deltaY < 0 ? 1.08 : 0.92))));
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  // Reset view on demand.
  useEffect(() => {
    setPan({ x: 0, y: 0 });
    setZoom(1);
  }, [resetToken]);

  // Build simulation when topology or canvas size changes.
  useEffect(() => {
    if (!size.w || !size.h || !nodes.length) return;
    const degree = new Map<string, number>();
    for (const e of edges) {
      degree.set(e.source, (degree.get(e.source) || 0) + 1);
      degree.set(e.target, (degree.get(e.target) || 0) + 1);
    }
    const simNodes: SimNode[] = nodes.map((n, i) => {
      const deg = degree.get(n.id) || 0;
      const isRepo = n.nodeType === "repository";
      const r = isRepo ? 22 : 6 + Math.min(deg * 1.4, 12);
      const angle = (i / Math.max(nodes.length, 1)) * Math.PI * 2;
      const radius = Math.min(size.w, size.h) / 2 - 70;
      return {
        ...n,
        x: size.w / 2 + Math.cos(angle) * radius * 0.7,
        y: size.h / 2 + Math.sin(angle) * radius * 0.7,
        vx: 0,
        vy: 0,
        r,
        mass: (isRepo ? 6 : 2) + deg * 0.4,
        fixed: false,
      };
    });
    const index = new Map<string, number>();
    simNodes.forEach((n, i) => index.set(n.id, i));
    const simEdges: SimEdge[] = edges
      .filter((e) => index.has(e.source) && index.has(e.target))
      .map((e) => ({
        s: index.get(e.source)!,
        t: index.get(e.target)!,
        rest: simNodes[index.get(e.source)!].r + simNodes[index.get(e.target)!].r + 110,
        weight: e.weight,
        virtual: e.virtual,
        relationship: e.relationship,
      }));
    if (simRef.current) cancelAnimationFrame(simRef.current.raf);
    simRef.current = { nodes: simNodes, edges: simEdges, index, alpha: 1, stopped: false, raf: 0 };
    const loop = () => {
      const sim = simRef.current;
      if (!sim) return;
      tick(sim);
      setFrame((f) => f + 1);
      if (!sim.stopped) sim.raf = requestAnimationFrame(loop);
    };
    simRef.current.raf = requestAnimationFrame(loop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphKey, size.w, size.h, nodes.length > 0]);

  const tick = (sim: {
    nodes: SimNode[];
    edges: SimEdge[];
    alpha: number;
    stopped: boolean;
  }) => {
    const ns = sim.nodes;
    const K = 2600;
    for (let i = 0; i < ns.length; i++) {
      for (let j = i + 1; j < ns.length; j++) {
        const a = ns[i];
        const b = ns[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 4) {
          dx = Math.random() - 0.5;
          dy = Math.random() - 0.5;
          d2 = dx * dx + dy * dy;
        }
        const d = Math.sqrt(d2) || 1;
        const f = Math.min((K * a.mass * b.mass) / d2, 70);
        const fx = (dx / d) * f;
        const fy = (dy / d) * f;
        a.vx += fx / a.mass;
        a.vy += fy / a.mass;
        b.vx -= fx / b.mass;
        b.vy -= fy / b.mass;
      }
    }
    for (const e of sim.edges) {
      const a = ns[e.s];
      const b = ns[e.t];
      if (a.fixed && b.fixed) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = 0.055 * (d - e.rest);
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      if (!a.fixed) {
        a.vx += fx / a.mass;
        a.vy += fy / a.mass;
      }
      if (!b.fixed) {
        b.vx -= fx / b.mass;
        b.vy -= fy / b.mass;
      }
    }
    const cx = size.w / 2;
    const cy = size.h / 2;
    for (const n of ns) {
      if (n.fixed) continue;
      n.vx += ((cx - n.x) * 0.02) / n.mass;
      n.vy += ((cy - n.y) * 0.02) / n.mass;
      n.vx *= 0.84;
      n.vy *= 0.84;
      const sp = Math.hypot(n.vx, n.vy);
      if (sp > 12) {
        n.vx = (n.vx / sp) * 12;
        n.vy = (n.vy / sp) * 12;
      }
      n.x += n.vx;
      n.y += n.vy;
      n.x = Math.max(24, Math.min(size.w - 24, n.x));
      n.y = Math.max(24, Math.min(size.h - 24, n.y));
    }
    sim.alpha *= 0.99;
    if (sim.alpha < 0.02) sim.stopped = true;
  };

  const reheat = () => {
    const sim = simRef.current;
    if (!sim) return;
    sim.alpha = Math.max(sim.alpha, 0.6);
    if (sim.stopped) {
      sim.stopped = false;
      const loop = () => {
        const s = simRef.current;
        if (!s) return;
        tick(s);
        setFrame((f) => f + 1);
        if (!s.stopped) s.raf = requestAnimationFrame(loop);
      };
      sim.raf = requestAnimationFrame(loop);
    }
  };

  // ── Pointer interactions ────────────────────────────────────

  const toSvgCoords = (clientX: number, clientY: number) => {
    const rect = svgRef.current!.getBoundingClientRect();
    return {
      x: (clientX - rect.left - pan.x) / zoom,
      y: (clientY - rect.top - pan.y) / zoom,
    };
  };

  const onBackgroundDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    svgRef.current?.setPointerCapture(e.pointerId);
    dragRef.current = {
      kind: "pan",
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      originX: pan.x,
      originY: pan.y,
      moved: 0,
    };
  };

  const onNodeDown = (e: React.PointerEvent, id: string) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    const sim = simRef.current;
    const idx = sim?.index.get(id);
    if (sim && idx !== undefined) sim.nodes[idx].fixed = true;
    svgRef.current?.setPointerCapture(e.pointerId);
    const { x, y } = toSvgCoords(e.clientX, e.clientY);
    dragRef.current = {
      kind: "node",
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      originX: x,
      originY: y,
      moved: 0,
      nodeId: id,
    };
    reheat();
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    d.moved = Math.max(d.moved, Math.hypot(dx, dy));
    if (d.kind === "pan") {
      setPan({ x: d.originX + dx, y: d.originY + dy });
    } else if (d.nodeId) {
      const sim = simRef.current;
      const idx = sim?.index.get(d.nodeId);
      if (sim && idx !== undefined) {
        const n = sim.nodes[idx];
        n.x = d.originX + dx / zoom;
        n.y = d.originY + dy / zoom;
        n.vx = 0;
        n.vy = 0;
      }
    }
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    if (d.kind === "node" && d.nodeId) {
      const sim = simRef.current;
      const idx = sim?.index.get(d.nodeId);
      if (sim && idx !== undefined) sim.nodes[idx].fixed = false;
      if (d.moved < 5) onSelect(d.nodeId);
    }
    dragRef.current = null;
  };

  const sim = simRef.current;
  const renderNodes = sim ? sim.nodes : [];
  const renderEdges = sim ? sim.edges : [];

  return (
    <div
      ref={wrapRef}
      className="relative h-[520px] rounded-xl overflow-hidden border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/60 touch-none"
    >
      {!nodes.length && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400 px-8 text-center">
          No nodes to visualize yet — query the graph or expand a node to see
          the force-directed neighborhood.
        </div>
      )}
      {nodes.length > 0 && (
        <svg
          ref={svgRef}
          width={size.w}
          height={size.h}
          className="w-full h-full select-none"
          onPointerDown={onBackgroundDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
        >
          <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
            {renderEdges.map((e, i) => {
              const a = renderNodes[e.s];
              const b = renderNodes[e.t];
              const highlighted =
                (hoveredId === a.id || selectedId === a.id) &&
                (hoveredId === b.id || selectedId === b.id);
              return (
                <line
                  key={i}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={e.virtual ? "#94a3b8" : highlighted ? "#6366f1" : "#94a3b8"}
                  strokeWidth={e.virtual ? 1 : 1 + e.weight * 2}
                  strokeDasharray={e.virtual ? "4 4" : undefined}
                  strokeOpacity={e.virtual ? 0.55 : 0.8}
                />
              );
            })}
            {renderNodes.map((n) => {
              const isSelected = selectedId === n.id;
              const isHovered = hoveredId === n.id;
              const showLabel = n.nodeType === "repository" || isSelected || isHovered;
              return (
                <g key={n.id}>
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r={n.r}
                    fill={hexFor(n.nodeType)}
                    stroke={isSelected ? "#312e81" : "rgba(255,255,255,0.65)"}
                    strokeWidth={isSelected ? 3 : 1.2}
                    cursor="grab"
                    onPointerDown={(e) => onNodeDown(e, n.id)}
                    onPointerEnter={() => onHover(n.id)}
                    onPointerLeave={() => onHover(null)}
                  />
                  {showLabel && (
                    <text
                      x={n.x}
                      y={n.y + n.r + 13}
                      textAnchor="middle"
                      fontSize={n.nodeType === "repository" ? 12 : 10}
                      fontWeight={isSelected ? 700 : 500}
                      fill="#334155"
                      className="dark:fill-slate-200 pointer-events-none"
                    >
                      {truncate(n.label, 26)}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      )}
      <div className="absolute bottom-2 left-2 text-[10px] font-mono text-slate-400 bg-white/70 dark:bg-slate-800/70 rounded px-2 py-1">
        drag canvas to pan · scroll to zoom · drag node to move
      </div>
    </div>
  );
}
