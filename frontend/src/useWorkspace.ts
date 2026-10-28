import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage, isAbort } from './api.ts';
import type { DocumentReceipt, Health, Job, SeedReceipt, SourceDocument } from './types.ts';

// A resource keeps its last successful value visibly separate from connection errors.
export function useWorkspace(accessRevision: number) {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [jobErrors, setJobErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const lifetime = useRef<AbortController | null>(null);
  const refreshInFlight = useRef<AbortSignal | null>(null);
  const documentRevision = useRef(0);

  const refresh = useCallback(async (quiet = false): Promise<void> => {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || refreshInFlight.current === signal) return;
    refreshInFlight.current = signal;
    const readRevision = documentRevision.current;
    if (!quiet) setRefreshing(true);
    const results = await Promise.allSettled([api.health(signal), api.documents(signal)]);
    if (!signal.aborted) {
      const [healthResult, documentResult] = results;
      if (healthResult.status === 'fulfilled') {
        setHealth(healthResult.value);
        setHealthError(null);
      } else if (!isAbort(healthResult.reason)) setHealthError(errorMessage(healthResult.reason));
      if (documentResult.status === 'fulfilled') {
        if (readRevision === documentRevision.current) setDocuments(documentResult.value);
        setDocumentsError(null);
      } else if (!isAbort(documentResult.reason)) setDocumentsError(errorMessage(documentResult.reason));
      setLoading(false);
      setRefreshing(false);
    }
    if (refreshInFlight.current === signal) refreshInFlight.current = null;
    if (!signal.aborted && readRevision !== documentRevision.current) void refresh(true);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    setHealth(null);
    setHealthError(null);
    setDocuments([]);
    setDocumentsError(null);
    setJobs({});
    setJobErrors({});
    setLoading(true);
    setRefreshing(false);
    void refresh();
    const timer = window.setInterval(() => void refresh(true), 15_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [accessRevision, refresh]);

  const activeJobIds = documents
    .filter((document) => document.status === 'queued' || document.status === 'processing')
    .flatMap((document) => document.job_id ? [document.job_id] : []).sort().join(',');
  const activeDocumentIds = documents
    .filter((document) => document.status === 'queued' || document.status === 'processing')
    .map((document) => document.id).sort().join(',');

  // Poll durable job receipts and document records while ingestion is active.
  useEffect(() => {
    if (!activeDocumentIds) return;
    const controller = new AbortController();
    let busy = false;
    const poll = async () => {
      if (busy || controller.signal.aborted) return;
      busy = true;
      const ids = activeJobIds ? activeJobIds.split(',') : [];
      const results = await Promise.allSettled(ids.map((id) => api.job(id, controller.signal)));
      if (!controller.signal.aborted) {
        results.forEach((result, index) => {
          const id = ids[index];
          if (result.status === 'fulfilled') {
            setJobs((previous) => ({ ...previous, [id]: result.value }));
            setJobErrors((previous) => { const next = { ...previous }; delete next[id]; return next; });
          } else if (!isAbort(result.reason)) {
            setJobErrors((previous) => ({ ...previous, [id]: errorMessage(result.reason) }));
          }
        });
      }
      try {
        const readRevision = documentRevision.current;
        const next = await api.documents(controller.signal);
        if (!controller.signal.aborted) {
          if (readRevision === documentRevision.current) setDocuments(next);
          setDocumentsError(null);
        }
      } catch (error) {
        if (!controller.signal.aborted && !isAbort(error)) setDocumentsError(errorMessage(error));
      }
      busy = false;
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2500);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [activeJobIds, activeDocumentIds, accessRevision]);

  const track = useCallback((receipt: DocumentReceipt | SeedReceipt) => {
    documentRevision.current += 1;
    const newDocuments = 'document' in receipt ? [receipt.document] : receipt.documents;
    const newJobs = 'job' in receipt ? [receipt.job] : receipt.jobs;
    setDocuments((previous) => {
      const byId = new Map(previous.map((document) => [document.id, document]));
      newDocuments.forEach((document) => byId.set(document.id, document));
      return [...byId.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
    });
    setJobs((previous) => ({ ...previous, ...Object.fromEntries(newJobs.map((job) => [job.id, job])) }));
  }, []);

  const forget = useCallback((id: string) => {
    documentRevision.current += 1;
    setDocuments((previous) => previous.filter((document) => document.id !== id));
  }, []);

  return { health, healthError, documents, documentsError, jobs, jobErrors, loading, refreshing, refresh, track, forget };
}

export type Workspace = ReturnType<typeof useWorkspace>;
