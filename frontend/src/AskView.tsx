import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage, isAbort } from './api.ts';
import { Alert, DocumentScope, Empty, Icon, Loading } from './components.tsx';
import ResultPanel from './ResultPanel.tsx';
import type { QueryRequest, QueryResult } from './types.ts';
import type { Workspace } from './useWorkspace.ts';
import { supportLabels, validateQuestion } from './utils.ts';

export default function AskView({ workspace, accessRevision, onDocuments }: {
  workspace: Workspace; accessRevision: number; onDocuments: () => void;
}) {
  const [question, setQuestion] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [topK, setTopK] = useState(6);
  const [hops, setHops] = useState(2);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [history, setHistory] = useState<QueryResult[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const historyLifetime = useRef<AbortController | null>(null);
  const queryLifetime = useRef<AbortController | null>(null);
  const canAsk = workspace.health?.pipeline_ready === true && !workspace.healthError && !workspace.documentsError && !workspace.loading;
  const readyIds = workspace.documents.filter((document) => document.status === 'ready').map((document) => document.id).sort().join(',');

  const refreshHistory = useCallback(async () => {
    const signal = historyLifetime.current?.signal;
    if (!signal || signal.aborted) return;
    setHistoryLoading(true);
    try {
      const queries = await api.history(signal);
      if (!signal.aborted) { setHistory(queries); setHistoryError(null); }
    } catch (historyFailure) {
      if (!signal.aborted && !isAbort(historyFailure)) setHistoryError(errorMessage(historyFailure));
    } finally { if (!signal.aborted) setHistoryLoading(false); }
  }, []);

  // History is read from the durable backend, including after a submitted query.
  useEffect(() => {
    const controller = new AbortController();
    historyLifetime.current = controller;
    setHistory([]);
    setHistoryError(null);
    setResult(null);
    setError(null);
    setPending(false);
    void refreshHistory();
    return () => { controller.abort(); queryLifetime.current?.abort(); };
  }, [accessRevision, refreshHistory]);

  useEffect(() => {
    if (!workspace.loading && !workspace.documentsError) {
      const available = new Set(readyIds ? readyIds.split(',') : []);
      setSelected((previous) => previous.filter((id) => available.has(id)));
    }
  }, [readyIds, workspace.loading, workspace.documentsError]);

  const ask = async () => {
    if (pending) return;
    const input: QueryRequest = { question, document_ids: selected, top_k: topK, hops };
    const invalid = validateQuestion(input);
    if (invalid) { setError(invalid); return; }
    if (!canAsk) { setError('The pipeline and document list must be available before asking a question. Check the connection status above.'); return; }
    const controller = new AbortController();
    queryLifetime.current?.abort();
    queryLifetime.current = controller;
    setError(null);
    setResult(null);
    setPending(true);
    try {
      const answer = await api.ask(input, controller.signal);
      if (!controller.signal.aborted) { setResult(answer); void refreshHistory(); }
    } catch (failure) {
      if (!controller.signal.aborted && !isAbort(failure)) {
        setError(`${errorMessage(failure)} If the request was interrupted, check recent queries before submitting it again.`);
        void refreshHistory();
      }
    } finally { if (!controller.signal.aborted) setPending(false); }
  };

  return <div className="ask-layout">
    <div className="ask-main">
      <p className="sr-only" role="status">{result && !pending ? `Answer ready. ${supportLabels[result.status]}. ${result.evidence.length} evidence excerpts and ${result.claims.length} audited claims.` : ''}</p>
      <section className="card question-card" aria-labelledby="question-heading">
        <div className="section-eyebrow">Start with a question</div><h2 id="question-heading">What would you like to verify?</h2>
        {workspace.documentsError && <Alert>Document scope is unavailable: {workspace.documentsError}<button type="button" className="text-button" disabled={workspace.refreshing} onClick={() => void workspace.refresh()}>Refresh document scope</button></Alert>}
        <form onSubmit={(event) => { event.preventDefault(); void ask(); }}>
          <label className="sr-only" htmlFor="question">Question</label>
          <textarea id="question" className="question-input" value={question} onChange={(event) => setQuestion(event.target.value)} rows={4} maxLength={2000} placeholder="Ask what your documents say about a topic, policy, or relationship…" aria-describedby="question-help" disabled={pending} required />
          <div id="question-help" className="question-help"><span>Answers are checked against retrieved source excerpts.</span><span>{question.length.toLocaleString()} / 2,000</span></div>
          <DocumentScope documents={workspace.documents} selected={selected} onChange={setSelected} disabled={pending || workspace.loading || !!workspace.documentsError} />
          <div className="retrieval-settings">
            <label className="field" htmlFor="top-k"><span>Evidence chunks (top-k)</span><input id="top-k" type="number" min={2} max={20} step={1} value={Number.isNaN(topK) ? '' : topK} onChange={(event) => setTopK(event.target.valueAsNumber)} disabled={pending} required aria-describedby="top-k-help" /><small id="top-k-help">Retrieve 2–20 excerpts.</small></label>
            <label className="field" htmlFor="hops"><span>Graph hops</span><select id="hops" value={hops} onChange={(event) => setHops(Number(event.target.value))} disabled={pending} aria-describedby="hops-help">{[1, 2, 3].map((value) => <option key={value} value={value}>{value} {value === 1 ? 'hop' : 'hops'}</option>)}</select><small id="hops-help">Expand 1–3 relationship hops.</small></label>
          </div>
          {error && <Alert>{error}</Alert>}
          <div className="form-footer"><span className="helper">{selected.length ? `${selected.length} documents in scope` : 'All indexed documents in scope'}</span><button className="button button-primary" type="submit" disabled={pending || !canAsk}>{pending ? <><span className="spinner spinner-light" aria-hidden="true" />Checking evidence</> : <>Ask with evidence<Icon name="arrow" size={18} /></>}</button></div>
        </form>
      </section>
      {pending ? <section className="card" aria-busy="true"><Loading>Retrieving evidence and auditing the answer…</Loading><p className="helper centered">The result will appear when the backend finishes retrieval and evaluation.</p></section> : result ? <ResultPanel result={result} /> : <section className="card answer-empty"><Empty title={readyIds ? 'An answer with a trail of evidence' : 'Your sources are the starting point'} icon="ask">{readyIds ? 'Ask a question to see a safe answer, source excerpts, and a claim-by-claim audit.' : 'Add documents or explicitly load the public samples. No documents are seeded automatically.'}</Empty>{!readyIds && <button type="button" className="button button-secondary" onClick={onDocuments}>Add documents<Icon name="arrow" size={16} /></button>}</section>}
    </div>
    <aside className="history-panel card" aria-labelledby="history-heading">
      <div className="card-heading"><div><div className="section-eyebrow">Saved on the backend</div><h2 id="history-heading">Recent queries</h2></div><button type="button" className="icon-button" aria-label="Refresh recent queries" onClick={() => void refreshHistory()} disabled={historyLoading}><Icon name="refresh" size={17} /></button></div>
      <p className="helper">The newest 20 results, including their original audit and evidence.</p>
      {historyError && <Alert>{historyError}{history.length > 0 && <span> Previously loaded history is shown below.</span>}</Alert>}
      {historyLoading && <Loading>Loading query history…</Loading>}
      {!historyLoading && !historyError && history.length === 0 && <p className="history-empty">No saved queries yet. Your first completed question will appear here.</p>}
      <ol className="history-list">{history.map((entry) => <li key={entry.id}><button type="button" aria-pressed={result?.id === entry.id} className={`history-entry ${result?.id === entry.id ? 'active' : ''}`} disabled={pending} onClick={() => { setResult(entry); setError(null); }}><strong>{entry.question}</strong><span className={`history-support status-${entry.status}`}>{supportLabels[entry.status]}</span><small>{entry.mode === 'demo' ? 'Demo' : 'Live'} · {entry.evidence.length} excerpts · {(entry.latency_ms / 1000).toFixed(2)} s</small></button></li>)}</ol>
    </aside>
  </div>;
}
