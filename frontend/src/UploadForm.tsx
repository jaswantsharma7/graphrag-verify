import { useEffect, useRef, useState } from 'react';
import { api, errorMessage, isAbort } from './api.ts';
import { Alert, Icon } from './components.tsx';
import type { Workspace } from './useWorkspace.ts';
import { ACCEPTED_FILES, documentLabels, formatBytes, MAX_NAME_CHARS, MAX_TEXT_CHARS, validateFile, validateName, validateText } from './utils.ts';

export default function UploadForm({ workspace, accessRevision }: { workspace: Workspace; accessRevision: number }) {
  const [tab, setTab] = useState<'file' | 'text'>('file');
  const [file, setFile] = useState<File | null>(null);
  const [fileName, setFileName] = useState('');
  const [textName, setTextName] = useState('');
  const [text, setText] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const lifetime = useRef<AbortController | null>(null);
  const canIngest = workspace.health?.pipeline_ready === true && !workspace.healthError;

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    setPending(false);
    setError(null);
    setMessage(null);
    return () => controller.abort();
  }, [accessRevision]);

  // A successful receipt is used directly, then reread from persisted documents.
  const submit = async () => {
    if (pending) return;
    const invalid = tab === 'file'
      ? validateFile(file) || validateName(fileName, false)
      : validateName(textName) || validateText(text);
    if (invalid) { setError(invalid); setMessage(null); return; }
    const signal = lifetime.current?.signal;
    if (!canIngest || !signal || signal.aborted) return;
    setPending(true);
    setError(null);
    setMessage(null);
    try {
      const receipt = tab === 'file' ? await api.upload(file!, fileName, signal) : await api.paste(textName, text, signal);
      if (!signal.aborted) {
        workspace.track(receipt);
        setMessage(`Request accepted: ${receipt.document.name}. Current status: ${documentLabels[receipt.document.status]}. Existing identical documents are reused.`);
        if (tab === 'file') { setFile(null); setFileName(''); if (input.current) input.current.value = ''; }
        else { setText(''); setTextName(''); }
        void workspace.refresh();
      }
    } catch (failure) {
      if (!signal.aborted && !isAbort(failure)) {
        setError(`${errorMessage(failure)} If the connection was interrupted, refresh the document list before trying again.`);
        void workspace.refresh();
      }
    } finally { if (!signal.aborted) setPending(false); }
  };

  return <section className="card upload-card" aria-labelledby="add-document-heading">
    <div className="section-eyebrow">Bring your own evidence</div><h2 id="add-document-heading">Add a document</h2>
    <div className="segmented-control" role="group" aria-label="Document input method"><button type="button" aria-pressed={tab === 'file'} className={tab === 'file' ? 'active' : ''} onClick={() => { setTab('file'); setError(null); }} disabled={pending}>Upload a file</button><button type="button" aria-pressed={tab === 'text'} className={tab === 'text' ? 'active' : ''} onClick={() => { setTab('text'); setError(null); }} disabled={pending}>Paste text</button></div>
    <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      {tab === 'file' ? <>
        <div className="file-drop"><span className="upload-icon"><Icon name="upload" size={25} /></span><label htmlFor="document-file">Choose a document to index</label><p className="helper">PDF, TXT, MD, or DOCX · up to 20 MB</p><input ref={input} id="document-file" type="file" accept={ACCEPTED_FILES} disabled={pending} aria-describedby="upload-bounds" onChange={(event) => {
          const selectedFile = event.target.files?.[0] ?? null;
          setFile(selectedFile);
          setFileName(selectedFile?.name ?? '');
          setError(validateFile(selectedFile));
          setMessage(null);
        }} />{file && <span className="helper selected-file">{file.name} · {formatBytes(file.size)}</span>}</div>
        <label className="field" htmlFor="file-name"><span>Document name <small>(optional)</small></span><input id="file-name" type="text" value={fileName} onChange={(event) => setFileName(event.target.value)} maxLength={MAX_NAME_CHARS} placeholder="Use the original filename" disabled={pending} /></label>
        <p id="upload-bounds" className="helper">One bounded file per request. The backend may enforce a smaller configured limit. Scanned PDFs need extractable text.</p>
      </> : <>
        <label className="field" htmlFor="text-name"><span>Document name</span><input id="text-name" type="text" value={textName} onChange={(event) => setTextName(event.target.value)} maxLength={MAX_NAME_CHARS} placeholder="For example, Project notes" disabled={pending} required /></label>
        <label className="field" htmlFor="document-text"><span>Source text</span><textarea id="document-text" rows={7} value={text} onChange={(event) => setText(event.target.value)} maxLength={MAX_TEXT_CHARS} placeholder="Paste the original source material here…" disabled={pending} required aria-describedby="text-bounds" /></label>
        <p id="text-bounds" className="helper">{text.length.toLocaleString()} / {MAX_TEXT_CHARS.toLocaleString()} characters. Source material is displayed as plain text.</p>
      </>}
      {error && <Alert>{error}</Alert>}{message && <Alert kind="success">{message}</Alert>}
      <div className="form-footer"><span className="helper">Saved first, indexed by a durable job.</span><button type="submit" className="button button-primary" disabled={pending || !canIngest}>{pending ? <><span className="spinner spinner-light" aria-hidden="true" />Submitting</> : <>Add document<Icon name="arrow" size={17} /></>}</button></div>
    </form>
  </section>;
}
