import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { readApiKey, saveApiKey } from './api.ts';
import type { SourceDocument } from './types.ts';

type IconName = 'ask' | 'documents' | 'graph' | 'key' | 'refresh' | 'arrow' | 'upload' | 'close' | 'check' | 'source';

// Decorative icons have no text content or third-party assets.
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    ask: <><path d="M21 11a8 8 0 0 1-8 8H6l-4 3V11a9 9 0 0 1 19 0Z" /><path d="M7 10h10M7 14h6" /></>,
    documents: <><path d="M14 2H5v20h14V7Z" /><path d="M14 2v5h5M8 12h8M8 16h6" /></>,
    graph: <><path d="m6 6 12 2M6 6l5 12M18 8l-7 10" /><circle cx="6" cy="6" r="3" /><circle cx="18" cy="8" r="3" /><circle cx="11" cy="18" r="3" /></>,
    key: <><circle cx="8" cy="8" r="5" /><path d="m12 12 9 9m-5-5 3-3m-6 0 3-3" /></>,
    refresh: <><path d="M20 7a9 9 0 1 0 1 9M20 2v5h-5" /></>,
    arrow: <><path d="M4 12h16m-6-6 6 6-6 6" /></>,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5" /></>,
    close: <><path d="m6 6 12 12M6 18 18 6" /></>,
    check: <><path d="m5 12 4 4L19 6" /></>,
    source: <><path d="M9 5H4v15h14v-5M13 3h8v8m0-8L10 14" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export function Loading({ children = 'Loading…' }: { children?: ReactNode }) {
  return <div className="loading-state" role="status"><span className="spinner" aria-hidden="true" />{children}</div>;
}

export function Empty({ title, children, icon = 'documents' }: { title: string; children: ReactNode; icon?: IconName }) {
  return <div className="empty-state"><span className="empty-icon"><Icon name={icon} size={26} /></span><h3>{title}</h3><p>{children}</p></div>;
}

export function Alert({ children, kind = 'error' }: { children: ReactNode; kind?: 'error' | 'info' | 'success' }) {
  return <div className={`alert alert-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>{children}</div>;
}

export function DocumentScope({ documents, selected, onChange, disabled = false }: {
  documents: SourceDocument[]; selected: string[]; onChange: (ids: string[]) => void; disabled?: boolean;
}) {
  const id = useId();
  const ready = documents.filter((document) => document.status === 'ready');
  return <details className="scope-picker">
    <summary><span className="field-caption">Document scope</span><strong>{selected.length ? `${selected.length} selected` : `All indexed documents (${ready.length})`}</strong></summary>
    <fieldset disabled={disabled} className="scope-options">
      <legend className="sr-only">Select indexed documents</legend>
      <button type="button" className="text-button" onClick={() => onChange([])} disabled={disabled || selected.length === 0}>Use all indexed documents</button>
      <p className="helper">An empty selection means all indexed documents. Queued and failed documents cannot be queried.</p>
      {ready.length === 0 ? <p className="helper">No indexed documents yet. Add documents to define a scope.</p> : ready.map((document) => <label className="scope-option" key={document.id} htmlFor={`${id}-${document.id}`}>
        <input id={`${id}-${document.id}`} type="checkbox" checked={selected.includes(document.id)} onChange={(event) => onChange(event.target.checked ? [...selected, document.id] : selected.filter((value) => value !== document.id))} />
        <span>{document.name}</span><small>{document.chunks} chunks</small>
      </label>)}
    </fieldset>
  </details>;
}

// Native dialogs provide focus containment and keyboard dismissal.
export function Modal({ title, children, onClose, busy = false }: {
  title: string; children: ReactNode; onClose: () => void; busy?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement;
    element?.showModal();
    return () => { element?.close(); if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  return <dialog ref={dialog} className="modal" aria-labelledby={titleId} onCancel={(event) => { if (busy) event.preventDefault(); }} onClose={() => { if (!dialog.current?.open) onClose(); }}>
    <div className="modal-heading"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" aria-label="Close dialog" onClick={onClose} disabled={busy}><Icon name="close" /></button></div>
    {children}
  </dialog>;
}

export function ApiSettings({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [value, setValue] = useState(readApiKey);
  const [error, setError] = useState<string | null>(null);
  const apply = (key: string) => {
    try { saveApiKey(key); setValue(''); onSaved(); onClose(); }
    catch { setError('Session storage is unavailable. Enable storage for this tab before saving an API key.'); }
  };
  return <Modal title="API access" onClose={onClose}>
    <p className="muted">If the backend requires authentication, enter its optional X-API-Key here.</p>
    <form onSubmit={(event) => { event.preventDefault(); apply(value); }}>
      <label className="field" htmlFor="api-key"><span>X-API-Key</span><input id="api-key" type="password" value={value} onChange={(event) => setValue(event.target.value)} autoComplete="off" spellCheck={false} maxLength={4096} aria-describedby="api-key-help" /></label>
      <p id="api-key-help" className="helper">Kept only in this tab’s sessionStorage. Sent only to this app’s same-origin /api routes. Clearing it removes the saved key.</p>
      {error && <Alert>{error}</Alert>}
      <div className="modal-actions"><button type="button" className="button button-ghost" onClick={() => apply('')}>Clear saved key</button><button type="submit" className="button button-primary">Save access settings</button></div>
    </form>
  </Modal>;
}
