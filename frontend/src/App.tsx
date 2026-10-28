import { useEffect, useState } from 'react';
import AskView from './AskView.tsx';
import DocumentsView from './DocumentsView.tsx';
import GraphView from './GraphView.tsx';
import { ApiSettings, Icon, Loading } from './components.tsx';
import { readApiKey } from './api.ts';
import { useWorkspace } from './useWorkspace.ts';

type Screen = 'ask' | 'documents' | 'graph';
const screens = {
  ask: { label: 'Ask', title: 'Ask. Inspect. Verify.', description: 'Follow every answer back to the evidence that supports it.' },
  documents: { label: 'Documents', title: 'A workspace built on sources.', description: 'Add, index, and manage the documents your answers depend on.' },
  graph: { label: 'Knowledge graph', title: 'See how your sources connect.', description: 'Explore scoped relationships and the source chunks behind them.' },
};

export default function App() {
  const [screen, setScreen] = useState<Screen>('ask');
  const [settings, setSettings] = useState(false);
  const [accessRevision, setAccessRevision] = useState(0);
  const [hasKey, setHasKey] = useState(() => !!readApiKey());
  const workspace = useWorkspace(accessRevision);
  const { health, healthError, loading } = workspace;
  const degraded = !!health && (health.status === 'degraded' || !health.pipeline_ready);
  const connected = !!health && !healthError && !degraded;
  const documentCount = !loading && !workspace.documentsError ? workspace.documents.length : null;
  const chooseScreen = (value: Screen) => {
    setScreen(value);
    window.scrollTo({ top: 0, behavior: 'auto' });
    window.requestAnimationFrame(() => document.getElementById('main-content')?.focus({ preventScroll: true }));
  };
  useEffect(() => { document.title = `${screens[screen].label} · GraphRAG Verify`; }, [screen]);

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to main content</a>
    <aside className="sidebar">
      <a className="brand" href="#main-content" onClick={() => chooseScreen('ask')} aria-label="GraphRAG Verify home"><span className="brand-mark"><Icon name="graph" size={25} /></span><span>GraphRAG<strong>Verify</strong></span></a>
      <div className="sidebar-section-label">Workspace</div>
      <nav className="main-nav" aria-label="Workspace navigation">{(['ask', 'documents', 'graph'] as const).map((value) => <button type="button" key={value} className={screen === value ? 'active' : ''} aria-current={screen === value ? 'page' : undefined} onClick={() => chooseScreen(value)}><Icon name={value} /><span>{screens[value].label}</span>{value === 'documents' && documentCount !== null && <span className="nav-count">{documentCount}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="workspace-status"><div className="status-heading"><span className={`status-dot ${connected ? 'connected' : healthError || degraded ? 'degraded' : ''}`} aria-hidden="true" /><strong>{loading ? 'Connecting' : healthError ? 'API unavailable' : degraded ? 'Pipeline degraded' : connected ? 'Pipeline ready' : 'Status unavailable'}</strong></div><span className="mode-label">{health ? `${health.mode === 'demo' ? 'Demo · extractive' : 'Live · model-backed'}${healthError ? ' (last known)' : ''}` : 'Mode unavailable'}</span>{health && <span className="helper">{health.stats.chunks} chunks · {health.stats.entities} entities{healthError ? ' (last known)' : ''}</span>}</div><p className="sidebar-note">Evidence support is a signal to inspect, not a guarantee of truth.</p></div>
    </aside>

    <main className="main-content" id="main-content" tabIndex={-1}>
      <header className="page-header"><div><div className="section-eyebrow">Source-grounded intelligence</div><h1>{screens[screen].title}</h1><p>{screens[screen].description}</p></div><div className="header-actions"><span className={`badge mode-badge ${healthError ? 'mode-stale' : ''}`}>{health ? `${health.mode === 'demo' ? 'Demo mode' : 'Live mode'}${healthError ? ' · last known' : ''}` : 'Mode unavailable'}</span><button type="button" className="button button-secondary api-access-button" onClick={() => setSettings(true)}><Icon name="key" size={16} /><span>API access</span>{hasKey && <span className="key-present" aria-label="Key saved in session" />}</button></div></header>

      {loading && <div className="connection-loading"><Loading>Checking backend connection…</Loading></div>}
      {(healthError || degraded) && <section className="connection-banner" role="alert"><div><strong>{healthError ? 'The backend could not be reached or authorized' : health?.mode === 'live' ? 'Live setup needs attention' : 'The indexing pipeline is not ready'}</strong><p>{healthError || health?.message || 'The backend reported a degraded pipeline.'}</p><p className="helper">{healthError ? 'Check that the API is running at the configured backend address. If authentication is enabled, set its X-API-Key in API access.' : health?.mode === 'live' ? 'Review the backend model, embedding, and Neo4j configuration, then retry the connection.' : 'Review the backend startup error and retry the connection.'}</p></div><button type="button" className="button button-secondary button-small" disabled={workspace.refreshing} onClick={() => void workspace.refresh()}><Icon name="refresh" size={15} />Retry connection</button></section>}
      {health?.mode === 'demo' && !healthError && <section className="demo-banner" aria-label="Demo mode explanation"><span className="demo-mark"><Icon name="source" size={19} /></span><div><strong>Extractive demo. Real sources, no simulated LLM.</strong><p>Demo mode uses deterministic extraction and retrieval. Add your own documents or explicitly load samples. Live mode requires configured models and Neo4j; both modes need source review.</p></div></section>}

      <div hidden={screen !== 'ask'}><AskView workspace={workspace} accessRevision={accessRevision} onDocuments={() => chooseScreen('documents')} /></div>
      <div hidden={screen !== 'documents'}><DocumentsView workspace={workspace} accessRevision={accessRevision} /></div>
      <div hidden={screen !== 'graph'}><GraphView workspace={workspace} visible={screen === 'graph'} accessRevision={accessRevision} /></div>
      <footer className="page-footer"><span>GraphRAG Verify</span><span>Inspect sources. Question the audit.{health && !healthError ? ` · API v${health.version}` : ''}</span></footer>
    </main>
    {settings && <ApiSettings onClose={() => setSettings(false)} onSaved={() => { setHasKey(!!readApiKey()); setAccessRevision((value) => value + 1); }} />}
  </div>;
}
