import { useEffect, useRef, useState } from 'react';
import { api, errorMessage, isAbort } from './api.ts';
import { Alert, Empty, Icon, Loading, Modal } from './components.tsx';
import UploadForm from './UploadForm.tsx';
import type { SourceDocument } from './types.ts';
import type { Workspace } from './useWorkspace.ts';
import { documentLabels, formatDate, percent } from './utils.ts';

export default function DocumentsView({ workspace, accessRevision }: { workspace: Workspace; accessRevision: number }) {
  const [filter, setFilter] = useState('all');
  const [seeding, setSeeding] = useState(false);
  const [actionId, setActionId] = useState<string | null>(null);
  const [target, setTarget] = useState<SourceDocument | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const lifetime = useRef<AbortController | null>(null);
  const { documents, jobs, jobErrors, documentsError, loading } = workspace;
  const ready = documents.filter((document) => document.status === 'ready').length;
  const active = documents.filter((document) => document.status === 'queued' || document.status === 'processing').length;
  const failed = documents.filter((document) => document.status === 'failed').length;
  const countsKnown = !loading && (!documentsError || documents.length > 0);
  const canIngest = workspace.health?.pipeline_ready === true && !workspace.healthError;

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    setSeeding(false);
    setActionId(null);
    setTarget(null);
    setDeleting(false);
    setDeleteError(null);
    setError(null);
    setMessage(null);
    return () => controller.abort();
  }, [accessRevision]);

  const seed = async () => {
    const signal = lifetime.current?.signal;
    if (seeding || !canIngest || !signal || signal.aborted) return;
    setSeeding(true); setError(null); setMessage(null);
    try {
      const receipt = await api.seed(signal);
      if (!signal.aborted) {
        workspace.track(receipt);
        setMessage(`Sample request completed: ${receipt.documents.length} document records returned. Existing samples are reused; ingestion status is shown below.`);
        void workspace.refresh();
      }
    } catch (failure) {
      if (!signal.aborted && !isAbort(failure)) { setError(errorMessage(failure)); void workspace.refresh(); }
    } finally { if (!signal.aborted) setSeeding(false); }
  };

  const retry = async (document: SourceDocument) => {
    const signal = lifetime.current?.signal;
    if (actionId || !signal || signal.aborted) return;
    setActionId(document.id); setError(null); setMessage(null);
    try {
      const receipt = await api.retry(document.id, signal);
      if (!signal.aborted) {
        workspace.track(receipt);
        setMessage(`Retry accepted for ${receipt.document.name}. Current status: ${documentLabels[receipt.document.status]}.`);
        void workspace.refresh();
      }
    } catch (failure) {
      if (!signal.aborted && !isAbort(failure)) { setError(errorMessage(failure)); void workspace.refresh(); }
    } finally { if (!signal.aborted) setActionId(null); }
  };

  // Deletion is issued only from the explicit confirmation dialog.
  const remove = async () => {
    const signal = lifetime.current?.signal;
    if (!target || deleting || !signal || signal.aborted) return;
    setDeleting(true); setDeleteError(null);
    try {
      await api.remove(target.id, signal);
      if (!signal.aborted) {
        workspace.forget(target.id);
        setMessage(`Deleted ${target.name} and its source provenance.`);
        setTarget(null);
        void workspace.refresh();
      }
    } catch (failure) {
      if (!signal.aborted && !isAbort(failure)) {
        setDeleteError(`${errorMessage(failure)} Refresh the list to verify the current state before trying again.`);
        void workspace.refresh();
      }
    } finally { if (!signal.aborted) setDeleting(false); }
  };

  const filtered = documents.filter((document) => filter === 'all' || (filter === 'active' ? document.status === 'queued' || document.status === 'processing' : document.status === filter));

  return <div className="documents-view">
    <div className="document-add-layout"><UploadForm workspace={workspace} accessRevision={accessRevision} /><section className="card sample-card" aria-labelledby="samples-heading"><span className="sample-graphic" aria-hidden="true"><Icon name="documents" size={34} /></span><div className="section-eyebrow">An explicit starting point</div><h2 id="samples-heading">Try the public samples</h2><p>Load the bundled source documents to explore questions, citations, and graph relationships.</p><p className="helper">This action adds sample documents to the backend. Nothing is seeded automatically. Repeated requests reuse existing samples.</p><button type="button" className="button button-secondary" disabled={seeding || !canIngest} onClick={() => void seed()}>{seeding ? <><span className="spinner" aria-hidden="true" />Loading samples</> : 'Load sample documents'}</button></section></div>
    <div className="document-metrics" role="group" aria-label={`Document status summary${documentsError ? ', last known values' : ''}`}><div><span className="metric-value">{countsKnown ? ready : '—'}</span><span>Indexed</span></div><div><span className="metric-value">{countsKnown ? active : '—'}</span><span>Queued / processing</span></div><div><span className="metric-value">{countsKnown ? failed : '—'}</span><span>Failed</span></div></div>
    {error && <Alert>{error}</Alert>}{message && <Alert kind="success">{message}</Alert>}
    <section className="card document-library" aria-labelledby="library-heading">
      <div className="card-heading library-heading"><div><div className="section-eyebrow">Persisted source library</div><h2 id="library-heading">Your documents <span className="count">{countsKnown ? documents.length : '—'}</span></h2></div><div className="library-controls"><label className="sr-only" htmlFor="document-filter">Filter document status</label><select id="document-filter" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">All statuses</option><option value="ready">Indexed</option><option value="active">Queued / processing</option><option value="failed">Failed</option></select><button type="button" className="icon-button" aria-label="Refresh document library" onClick={() => void workspace.refresh()} disabled={workspace.refreshing}><Icon name="refresh" /></button></div></div>
      <p className="helper">Active jobs are polled every 2.5 seconds. Documents and status survive backend restarts; interrupted jobs may need a retry.</p>
      {documentsError && <Alert>{documentsError}{documents.length > 0 && <span> Previously loaded documents are shown; their status may be out of date.</span>}</Alert>}
      {loading ? <Loading>Loading persisted documents…</Loading> : filtered.length === 0 && !documentsError ? <Empty title={documents.length ? 'No documents match this status' : 'Your source library is empty'}>{documents.length ? 'Choose another status filter to view your documents.' : 'Upload a file, paste text, or explicitly load samples to create your first indexed document.'}</Empty> : <ul className="document-list">{filtered.map((document) => {
        const job = document.job_id ? jobs[document.job_id] : undefined;
        const jobError = document.job_id ? jobErrors[document.job_id] : undefined;
        const processing = document.status === 'queued' || document.status === 'processing';
        return <li key={document.id} className="document-row">
          <div className="document-row-heading"><span className="document-icon"><Icon name="documents" /></span><div className="document-title"><h3>{document.name}</h3><span className="helper">Added {formatDate(document.created_at)}</span></div><span className={`badge document-${document.status}`}>{documentLabels[document.status]}</span></div>
          <div className="document-row-meta"><span>{document.chunks} chunks</span><span>{document.entities} entities</span><span>{document.relationships} relationships</span></div>
          {processing && <div className="job-progress">{job ? <><div><span>{job.stage || 'Stage not reported'} · {job.status}</span><strong>{percent(job.progress)}</strong></div><progress max={1} value={job.progress} aria-label={`${document.name} ingestion progress`} /><span className="helper">Last job update {formatDate(job.updated_at)}</span></> : <p className="helper">Saved status: {documentLabels[document.status]}. Waiting for a job progress receipt.</p>}</div>}
          {(document.error || job?.error) && <div className="document-error" role="alert">{document.error || job?.error}</div>}
          {processing && jobError && <p className="document-error" role="alert">Job progress unavailable: {jobError}</p>}
          <details className="document-details"><summary>Document and job IDs</summary><div className="helper">Document: <code>{document.id}</code></div><div className="helper">Job: {document.job_id ? <code>{document.job_id}</code> : 'No job ID returned'}</div></details>
          <div className="document-actions">{document.status === 'failed' && <button type="button" className="button button-secondary button-small" disabled={!canIngest || !!actionId || !!documentsError} onClick={() => void retry(document)}>{actionId === document.id ? 'Retrying…' : 'Retry indexing'}</button>}<button type="button" className="button button-ghost button-small danger-text" disabled={processing || deleting || !!actionId || !!documentsError} title={processing ? 'Wait for ingestion to finish before deleting.' : undefined} onClick={() => { setTarget(document); setDeleteError(null); }}>Delete</button></div>
        </li>;
      })}</ul>}
    </section>
    {target && <Modal title="Delete this document?" busy={deleting} onClose={() => { if (!deleting) setTarget(null); }}><p>Remove <strong>{target.name}</strong>, its chunks, and graph provenance from the backend?</p><p className="helper">This cannot be undone.</p>{deleteError && <Alert>{deleteError}</Alert>}<div className="modal-actions"><button type="button" className="button button-ghost" disabled={deleting} onClick={() => setTarget(null)}>Keep document</button><button type="button" className="button button-danger" disabled={deleting} onClick={() => void remove()}>{deleting ? 'Deleting…' : 'Delete document'}</button></div></Modal>}
  </div>;
}
