import { useEffect, useMemo, useRef } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import type { AgentCard, FlowEdge } from "../api";
import { LAYER_ICON, NODES } from "../graphDef";

interface AgentNodeData extends Record<string, unknown> {
  title: string;
  sub: string;
  layer: string;
  received: number;
  produced: number;
  hot: boolean;
  dim: boolean;
  running: boolean;
  purpose: string;
}

function AgentNode({ data }: NodeProps<Node<AgentNodeData>>) {
  return (
    <div className={`anode ${data.layer} ${data.hot ? "hot" : ""} ${data.dim ? "dim" : ""}`} title={data.purpose}>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="t">
        <span className={`icon i-${data.layer}`}>{LAYER_ICON[data.layer]}</span>
        {data.title}
        {data.running && <span className="dot warn" title="working" />}
      </div>
      <div className="s">{data.sub}</div>
      <div className="c">
        in {data.received} · out {data.produced}
      </div>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

export interface GraphProps {
  flow: FlowEdge[];
  agents: AgentCard[];
  /** Nodes to light up (the step being replayed, or whatever is active right now). */
  active?: Set<string>;
  /** Edges to light up, as "source>target". */
  activeEdges?: Set<string>;
  /** When set, everything except these nodes is faded (a filter, or the run's own path). */
  focus?: Set<string> | null;
  onNodeClick?: (id: string) => void;
  height?: number | string;
}

export function AgentGraph({ flow, agents, active, activeEdges, focus, onNodeClick, height = "100%" }: GraphProps) {
  const nodes = useMemo<Node<AgentNodeData>[]>(
    () =>
      NODES.map((n) => {
        const card = agents.find((a) => a.name === n.id);
        return {
          id: n.id,
          type: "agent",
          position: { x: n.x, y: n.y },
          draggable: false,
          data: {
            title: n.title,
            sub: n.sub,
            layer: n.layer,
            received: card?.received ?? 0,
            produced: card?.produced ?? 0,
            hot: active?.has(n.id) ?? false,
            running: active?.has(n.id) ?? false,
            dim: focus ? !focus.has(n.id) : false,
            purpose: card?.purpose ?? "",
          },
        };
      }),
    [agents, active, focus],
  );

  const edges = useMemo<Edge[]>(() => {
    const merged = new Map<string, { source: string; target: string; kind: string; count: number; labels: Record<string, number> }>();
    for (const e of flow) {
      const key = `${e.source}>${e.target}>${e.kind === "delivered" ? "d" : e.kind}`;
      const m = merged.get(key) ?? { source: e.source, target: e.target, kind: e.kind, count: 0, labels: {} };
      m.count += e.count;
      m.labels[e.label] = (m.labels[e.label] ?? 0) + e.count;
      merged.set(key, m);
    }
    const known = new Set(NODES.map((n) => n.id));
    return [...merged.entries()]
      .filter(([, m]) => known.has(m.source) && known.has(m.target))
      .map(([key, m]) => {
        const hot = activeEdges?.has(`${m.source}>${m.target}`) ?? false;
        const dim = focus ? !(focus.has(m.source) && focus.has(m.target)) : false;
        const colour = hot ? "var(--hot)" : m.kind === "execute" ? "var(--act)" : m.kind === "review" ? "var(--control)" : "var(--mut)";
        return {
          id: key,
          source: m.source,
          target: m.target,
          type: "default",
          animated: hot,
          label: String(m.count),
          labelStyle: { fontSize: 10, fill: "var(--mut)" },
          labelBgStyle: { fill: "var(--bg)" },
          style: { stroke: colour, strokeWidth: hot ? 3 : Math.min(1.2 + Math.log2(m.count + 1), 4), opacity: dim ? 0.12 : hot ? 1 : 0.6 },
          markerEnd: { type: MarkerType.ArrowClosed, color: colour, width: 14, height: 14 },
          data: { labels: m.labels },
        } as Edge;
      });
  }, [flow, activeEdges, focus]);

  const box = useRef<HTMLDivElement>(null);
  const instance = useRef<ReactFlowInstance<Node<AgentNodeData>, Edge> | null>(null);
  // Fit the graph to its box whenever the box changes size (panes resize, windows resize).
  useEffect(() => {
    if (!box.current) return;
    const observer = new ResizeObserver(() => instance.current?.fitView({ padding: 0.1, duration: 150 }));
    observer.observe(box.current);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={box} style={{ height, width: "100%" }}>
      <ReactFlow
        onInit={(flow) => {
          instance.current = flow;
          flow.fitView({ padding: 0.1 });
        }}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, node) => onNodeClick?.(node.id)}
        minZoom={0.3}
      >
        <Background gap={24} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
