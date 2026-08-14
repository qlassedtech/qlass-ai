export type FlowTone = "neutral" | "accent" | "success" | "error";

export interface FlowNode {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  subtitle?: string;
  tone?: FlowTone;
}

export interface FlowEdge {
  points: [number, number][];
  bidirectional?: boolean;
}

interface FlowDiagramProps {
  width?: number;
  height: number;
  nodes: FlowNode[];
  edges: FlowEdge[];
  title: string;
  desc: string;
}

const TONE_STYLES: Record<FlowTone, { fill: string; stroke: string; text: string }> = {
  neutral: { fill: "var(--table-header-bg)", stroke: "var(--border)", text: "var(--text-muted)" },
  accent: { fill: "var(--accent-light)", stroke: "var(--accent)", text: "var(--accent-dark)" },
  success: { fill: "var(--success-tint)", stroke: "var(--success)", text: "var(--success)" },
  error: { fill: "var(--error-tint)", stroke: "var(--error)", text: "var(--error)" },
};

function pathFor(points: [number, number][]): string {
  return points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x},${y}`).join(" ");
}

export default function FlowDiagram({ width = 680, height, nodes, edges, title, desc }: FlowDiagramProps) {
  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} role="img" className="flow-diagram">
      <title>{title}</title>
      <desc>{desc}</desc>
      <defs>
        <marker id="flow-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M2 1L8 5L2 9" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </marker>
      </defs>

      {edges.map((edge, i) => (
        <path
          key={i}
          d={pathFor(edge.points)}
          fill="none"
          stroke="var(--text-muted)"
          strokeWidth="1.25"
          markerEnd="url(#flow-arrow)"
          markerStart={edge.bidirectional ? "url(#flow-arrow)" : undefined}
        />
      ))}

      {nodes.map((node) => {
        const tone = TONE_STYLES[node.tone || "neutral"];
        const cx = node.x + node.w / 2;
        return (
          <g key={node.id}>
            <rect x={node.x} y={node.y} width={node.w} height={node.h} rx="8" fill={tone.fill} stroke={tone.stroke} strokeWidth="1" />
            <text x={cx} y={node.y + node.h / 2 + (node.subtitle ? -4 : 5)} textAnchor="middle" fontSize="14" fontWeight="600" fill={tone.text}>
              {node.title}
            </text>
            {node.subtitle && (
              <text x={cx} y={node.y + node.h / 2 + 14} textAnchor="middle" fontSize="12" fill="var(--text-muted)">
                {node.subtitle}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
