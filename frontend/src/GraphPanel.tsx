import { useEffect, useId, useMemo, useState } from 'react';
import { Empty } from './components.tsx';
import type { GraphNode, GraphView } from './types.ts';
import { evidenceAnchor } from './utils.ts';

const palette = ['#1768ef', '#087f9b', '#6354c8', '#234a79'];
function kindColor(kind: string): string {
  return palette[[...kind].reduce((sum, character) => sum + character.charCodeAt(0), 0) % palette.length];
}

// Deterministic SVG placement keeps the graph dependency-free.
function positions(nodes: GraphNode[]): Map<string, { x: number; y: number }> {
  return new Map<string, { x: number; y: number }>(nodes.map((node, index) => {
    if (nodes.length === 1) return [node.id, { x: 380, y: 230 }];
    const angle = nodes.length <= 18 ? index * 2 * Math.PI / nodes.length - Math.PI / 2 : index * 2.399963;
    const radius = nodes.length <= 18 ? 165 : 45 + 155 * Math.sqrt(index / Math.max(1, nodes.length - 1));
    return [node.id, { x: 380 + Math.cos(angle) * radius * 1.55, y: 230 + Math.sin(angle) * radius }];
  }));
}

export default function GraphPanel({ graph, citationIds = [] }: { graph: GraphView; citationIds?: string[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  const id = useId().replace(/:/g, '');
  const points = useMemo(() => positions(graph.nodes), [graph.nodes]);
  const labels = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node.label])), [graph.nodes]);
  const kinds = [...new Set(graph.nodes.map((node) => node.kind))];
  useEffect(() => { if (selected && !points.has(selected)) setSelected(null); }, [points, selected]);

  if (!graph.nodes.length && !graph.edges.length) return <Empty title="No graph relationships to display" icon="graph">The backend returned an empty graph for this scope. Try indexing more documents or choosing another scope.</Empty>;
  return <div className="graph-panel">
    <div className="graph-toolbar"><span className="helper">{graph.nodes.length} nodes · {graph.edges.length} relationships</span>{selected && <button className="text-button" type="button" onClick={() => setSelected(null)}>Clear node highlight</button>}</div>
    <figure className="graph-figure">
      <svg viewBox="0 0 760 470" role="img" aria-labelledby={`${id}-title ${id}-description`}>
        <title id={`${id}-title`}>Knowledge graph with {graph.nodes.length} nodes and {graph.edges.length} directed relationships</title>
        <desc id={`${id}-description`}>Use the node list below to highlight a node. The relationship list gives full labels, endpoints, and source evidence identifiers.</desc>
        <defs><marker id={`${id}-arrow`} viewBox="0 0 10 10" refX="16" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10" fill="#86a7d3" /></marker></defs>
        {graph.edges.map((edge) => {
          const source = points.get(edge.source);
          const target = points.get(edge.target);
          if (!source || !target) return null;
          const related = !selected || edge.source === selected || edge.target === selected;
          return <g key={edge.id} opacity={related ? 0.8 : 0.14}>
            {edge.source === edge.target
              ? <path d={`M${source.x - 8} ${source.y - 5} c-30 -42 45 -42 16 0`} fill="none" stroke="#86a7d3" strokeWidth="1.4" markerEnd={`url(#${id}-arrow)`} />
              : <line x1={source.x} y1={source.y} x2={target.x} y2={target.y} stroke="#86a7d3" strokeWidth={related && selected ? 2 : 1.2} markerEnd={`url(#${id}-arrow)`} />}
            <title>{labels.get(edge.source) ?? edge.source} to {labels.get(edge.target) ?? edge.target}: {edge.label}</title>
          </g>;
        })}
        {graph.nodes.map((node) => {
          const point = points.get(node.id)!;
          const highlighted = selected === node.id;
          return <g key={node.id} opacity={selected && !highlighted ? 0.5 : 1}>
            {highlighted && <circle cx={point.x} cy={point.y} r="19" fill="#dbeaff" />}
            <circle cx={point.x} cy={point.y} r={graph.nodes.length > 40 ? 7 : 10} fill={kindColor(node.kind)} stroke="white" strokeWidth="2" />
            {(graph.nodes.length <= 18 || highlighted) && <text x={point.x} y={point.y + 26} textAnchor="middle" className="graph-node-label">{node.label.length > 24 ? `${node.label.slice(0, 23)}…` : node.label}</text>}
            <title>{node.label} ({node.kind}), {node.id}</title>
          </g>;
        })}
      </svg>
      <figcaption>Directed links represent extracted relationships, not verified facts. Full provenance is listed below.</figcaption>
    </figure>
    <div className="graph-legend" aria-label="Node kinds">{kinds.map((kind) => <span key={kind}><i style={{ background: kindColor(kind) }} aria-hidden="true" />{kind}</span>)}</div>
    <div className="graph-lists">
      <details open><summary>Nodes <span className="count">{graph.nodes.length}</span></summary>
        <ul className="node-list">{graph.nodes.map((node) => <li key={node.id}><button type="button" className={`node-button ${selected === node.id ? 'selected' : ''}`} aria-pressed={selected === node.id} onClick={() => setSelected(selected === node.id ? null : node.id)}><strong>{node.label}</strong><span>{node.kind}</span><code>{node.id}</code></button></li>)}</ul>
      </details>
      <details open><summary>Relationships <span className="count">{graph.edges.length}</span></summary>
        {graph.edges.length === 0 ? <p className="helper list-padding">No relationships returned for these nodes.</p> : <ul className="edge-list">{graph.edges.map((edge) => <li key={edge.id} className={selected && (edge.source === selected || edge.target === selected) ? 'highlighted' : ''}>
          <strong>{edge.label}</strong><p>{labels.get(edge.source) ?? edge.source} <span className="muted">to</span> {labels.get(edge.target) ?? edge.target}</p>
          <div className="helper">Relationship ID: <code>{edge.id}</code></div>
          <div className="helper">Evidence IDs: {edge.evidence_ids.length === 0 ? 'None returned' : edge.evidence_ids.map((evidenceId, index) => <span key={`${evidenceId}-${index}`}>{index > 0 && ', '}{citationIds.includes(evidenceId) ? <a href={`#${evidenceAnchor(evidenceId)}`}><code>{evidenceId}</code></a> : <code>{evidenceId}</code>}</span>)}</div>
        </li>)}</ul>}
      </details>
    </div>
  </div>;
}
