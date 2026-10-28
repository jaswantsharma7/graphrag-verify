import GraphPanel from './GraphPanel.tsx';
import { Empty } from './components.tsx';
import type { ClaimStatus, QueryResult, SupportStatus } from './types.ts';
import { evidenceAnchor, percent, supportLabels } from './utils.ts';

const explanations: Record<SupportStatus, string> = {
  grounded: 'The audit found support in the retrieved excerpts. It does not establish that the underlying sources are true.',
  partial: 'Only some claims were supported by the retrieved evidence. Review the safe answer and audit before relying on it.',
  ungrounded: 'The draft did not have enough evidence support. Unsupported content should not be treated as an answer.',
  no_evidence: 'No usable evidence was retrieved for this question and scope. The system should abstain rather than invent an answer.',
};
const claimLabels: Record<ClaimStatus, string> = { supported: 'Supported', unsupported: 'Unsupported', contradicted: 'Contradicted' };

// Source text and model output are always rendered as plain text.
export default function ResultPanel({ result }: { result: QueryResult }) {
  const evidenceIndex = new Map(result.evidence.map((evidence, index) => [evidence.id, index + 1]));
  return <div className="result-stack">
    <section className="card answer-card" aria-labelledby="answer-heading">
      <div className="section-eyebrow">Safe answer</div>
      <h2 id="answer-heading">{result.question}</h2>
      <div className="answer-status-row"><span className={`badge status-${result.status}`}>{supportLabels[result.status]}</span><span className="helper">{result.mode === 'demo' ? 'Extractive demo' : 'Live pipeline'} · {(result.latency_ms / 1000).toFixed(2)} s</span></div>
      <p className="answer-text">{result.answer || 'The backend returned no safe answer.'}</p>
      <div className="support-meter"><div><strong>Evidence support</strong><span>{percent(result.score)}</span></div><meter min={0} max={1} value={result.score} aria-label={`Evidence support: ${percent(result.score)}`} /></div>
      <p className="helper">{explanations[result.status]} This score is not a probability of truth; the evaluator can be wrong.</p>
      {result.evidence.length > 0 && <div className="citation-chips" aria-label="Retrieved evidence citations">{result.evidence.map((evidence, index) => <a className="citation-chip" href={`#${evidenceAnchor(evidence.id)}`} key={evidence.id}>E{index + 1} · {evidence.document_name}{evidence.page !== null ? ` · p. ${evidence.page}` : ''}</a>)}</div>}
    </section>

    <section className="card" aria-labelledby="audit-heading">
      <div className="card-heading"><div><div className="section-eyebrow">Inspect the reasoning</div><h2 id="audit-heading">Claim audit</h2></div><span className="count">{result.claims.length}</span></div>
      <p className="helper">Each draft claim is assessed against cited excerpts. Read the reasons and original evidence; an automated audit is fallible.</p>
      {result.claims.length === 0 ? <Empty title="No claims returned" icon="ask">There are no claim-level audit records for this result.</Empty> : <ol className="claim-list">{result.claims.map((claim, index) => <li key={index}>
        <div className="claim-heading"><span className="claim-number">{index + 1}</span><span className={`badge claim-${claim.status}`}>{claimLabels[claim.status]}</span></div>
        <p className="claim-text">{claim.text}</p><p className="helper claim-reason">{claim.reason || 'No reason supplied by the backend.'}</p>
        <div className="claim-citations"><span className="helper">Citations:</span>{claim.citations.length === 0 ? <span className="helper">None</span> : claim.citations.map((citation, citationIndex) => evidenceIndex.has(citation)
          ? <a className="citation-chip" key={`${citation}-${citationIndex}`} href={`#${evidenceAnchor(citation)}`} title={citation}>E{evidenceIndex.get(citation)}</a>
          : <span className="invalid-citation" key={`${citation}-${citationIndex}`}>Unavailable evidence: <code>{citation}</code></span>)}</div>
      </li>)}</ol>}
    </section>

    <section className="card" aria-labelledby="evidence-heading">
      <div className="card-heading"><div><div className="section-eyebrow">Source provenance</div><h2 id="evidence-heading">Evidence excerpts</h2></div><span className="count">{result.evidence.length}</span></div>
      <p className="helper">Retrieval scores rank excerpts; they are separate from answer support and are not truth probabilities. Page numbers appear only when the source provides them.</p>
      {result.evidence.length === 0 ? <Empty title="No evidence returned" icon="source">Try another question, add source material, or broaden the document scope.</Empty> : <div className="evidence-list">{result.evidence.map((evidence, index) => <article className="evidence-card" id={evidenceAnchor(evidence.id)} key={evidence.id} tabIndex={-1}>
        <div className="evidence-heading"><span className="evidence-number">E{index + 1}</span><div><h3>{evidence.document_name}</h3><span className="helper">{evidence.page !== null ? `Page ${evidence.page}` : 'Page not available'} · {evidence.retrieval} retrieval · score {evidence.score.toFixed(3)}</span></div></div>
        <blockquote>{evidence.text}</blockquote>
        <details className="provenance"><summary>Evidence details</summary><dl><dt>Evidence ID</dt><dd><code>{evidence.id}</code></dd><dt>Document ID</dt><dd><code>{evidence.document_id}</code></dd><dt>Entity IDs</dt><dd>{evidence.entity_ids.length ? evidence.entity_ids.map((entity, entityIndex) => <span key={`${entity}-${entityIndex}`}>{entityIndex > 0 && ', '}<code>{entity}</code></span>) : 'None returned'}</dd></dl></details>
      </article>)}</div>}
    </section>

    <details className="card draft-card"><summary><span>Unverified draft</span><span className="helper">Expand to inspect</span></summary><p className="draft-warning">This is the pre-gating draft. It can contain unsupported or contradicted claims, even when the safe answer above is supported.</p><p className="source-text">{result.draft || 'No draft returned.'}</p></details>

    <details className="card result-graph"><summary>Retrieved knowledge graph <span className="count">{result.graph.nodes.length} nodes</span></summary><p className="helper">This is the graph returned with this query, scoped by the backend retrieval. Relationships retain chunk-level evidence IDs.</p><GraphPanel graph={result.graph} citationIds={result.evidence.map((evidence) => evidence.id)} /></details>
  </div>;
}
