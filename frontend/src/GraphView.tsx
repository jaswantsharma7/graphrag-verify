import { useEffect, useState } from 'react';
import { api, errorMessage, isAbort } from './api.ts';
import { Alert, DocumentScope, Empty, Icon, Loading } from './components.tsx';
import GraphPanel from './GraphPanel.tsx';
import type { GraphView as GraphData } from './types.ts';
import type { Workspace } from './useWorkspace.ts';

export default function GraphView({ workspace, visible, accessRevision }: { workspace: Workspace; visible: boolean; accessRevision: number }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [limit, setLimit] = useState(100);
  const [revision, setRevision] = useState(0);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const readyIds = workspace.documents.filter((document) => document.status === 'ready').map((document) => document.id).sort().join(',');
  const scope = selected.join(',');
  const canRead = workspace.health?.pipeline_ready === true && !workspace.healthError && !workspace.documentsError && !workspace.loading;

  useEffect(() => {
    if (!workspace.loading && !workspace.documentsError) {
      const available = new Set(readyIds ? readyIds.split(',') : []);
      setSelected((previous) => previous.filter((id) => available.has(id)));
    }
  }, [readyIds, workspace.loading, workspace.documentsError]);

  // Every visualization comes from a scoped graph API response.
  useEffect(() => {
    if (!visible || !canRead) return;
    const available = new Set(readyIds ? readyIds.split(',') : []);
    const ids = scope ? scope.split(',') : [];
    if (ids.some((id) => !available.has(id))) return;
    const controller = new AbortController();
    setGraph(null);
    setLoading(true);
    setError(null);
    api.graph(ids, limit, controller.signal).then((value) => {
      if (!controller.signal.aborted) setGraph(value);
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted && !isAbort(failure)) setError(errorMessage(failure));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [visible, canRead, scope, limit, revision, accessRevision, readyIds]);

  return <section className="card graph-view" aria-labelledby="graph-view-heading">
    <div className="card-heading"><div><div className="section-eyebrow">Explore source-linked relationships</div><h2 id="graph-view-heading">Knowledge graph</h2></div><button type="button" className="button button-secondary button-small" onClick={() => setRevision((value) => value + 1)} disabled={loading || !canRead}><Icon name="refresh" size={16} />Refresh graph</button></div>
    <p className="helper">The graph is an extracted view of your sources, not an assertion of truth. Evidence IDs identify the source chunks behind each relationship.</p>
    <div className="graph-filters"><DocumentScope documents={workspace.documents} selected={selected} onChange={setSelected} disabled={!canRead} /><label className="field" htmlFor="graph-limit"><span>Graph limit</span><select id="graph-limit" value={limit} onChange={(event) => setLimit(Number(event.target.value))} disabled={!canRead}>{[25, 50, 100].map((value) => <option value={value} key={value}>{value}</option>)}</select><small>Requested cap; at most 100 in this UI.</small></label></div>
    <p className="helper graph-limit-note">Scoped to {selected.length ? `${selected.length} selected indexed documents` : 'all indexed documents'}. A capped response may omit relationships; it is not the full graph. Graph limit is separate from question top-k and hops.</p>
    {workspace.documentsError && <Alert>Document scope is unavailable: {workspace.documentsError}<button type="button" className="text-button" disabled={workspace.refreshing} onClick={() => void workspace.refresh()}>Refresh document scope</button></Alert>}
    {error && <Alert>{error}<button type="button" className="text-button" onClick={() => setRevision((value) => value + 1)}>Try again</button></Alert>}
    {workspace.loading ? <Loading>Loading document scope…</Loading> : !canRead ? <Empty title="The graph needs an available pipeline" icon="graph">Check the connection and setup status above, then refresh to load the current graph.</Empty> : loading ? <Loading>Loading graph and provenance…</Loading> : graph ? <GraphPanel graph={graph} /> : !error ? <Empty title="Choose a document scope" icon="graph">The backend graph will appear here once it can be loaded.</Empty> : null}
  </section>;
}
