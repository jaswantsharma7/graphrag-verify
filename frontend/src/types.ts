// These transport types mirror docs/CONTRACT.md.
export type Mode = 'demo' | 'live';
export type DocumentStatus = 'queued' | 'processing' | 'ready' | 'failed';
export type JobStatus = 'queued' | 'processing' | 'completed' | 'failed';
export type SupportStatus = 'grounded' | 'partial' | 'ungrounded' | 'no_evidence';
export type ClaimStatus = 'supported' | 'unsupported' | 'contradicted';

export interface Health {
  status: 'ok' | 'degraded';
  mode: Mode;
  version: string;
  pipeline_ready: boolean;
  message: string;
  stats: { documents: number; chunks: number; entities: number; relationships: number };
}

export interface SourceDocument {
  id: string;
  name: string;
  status: DocumentStatus;
  created_at: string;
  chunks: number;
  entities: number;
  relationships: number;
  error: string | null;
  job_id: string | null;
}

export interface Job {
  id: string;
  document_id: string;
  status: JobStatus;
  stage: string;
  progress: number;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentReceipt {
  document: SourceDocument;
  job: Job;
}

export interface SeedReceipt {
  documents: SourceDocument[];
  jobs: Job[];
}

export interface Evidence {
  id: string;
  document_id: string;
  document_name: string;
  page: number | null;
  text: string;
  score: number;
  retrieval: 'vector' | 'graph' | 'lexical';
  entity_ids: string[];
}

export interface Claim {
  text: string;
  status: ClaimStatus;
  citations: string[];
  reason: string;
}

export interface GraphNode { id: string; label: string; kind: string }
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  evidence_ids: string[];
}
export interface GraphView { nodes: GraphNode[]; edges: GraphEdge[] }

export interface QueryRequest {
  question: string;
  document_ids: string[];
  top_k: number;
  hops: number;
}

export interface QueryResult {
  id: string;
  question: string;
  answer: string;
  draft: string;
  status: SupportStatus;
  score: number;
  claims: Claim[];
  evidence: Evidence[];
  graph: GraphView;
  latency_ms: number;
  mode: Mode;
}
