import type {
  DocumentReceipt, GraphView, Health, Job, QueryRequest, QueryResult,
  SeedReceipt, SourceDocument,
} from './types.ts';

const SESSION_KEY = 'graphrag-verify.api-key';

// Credentials are read only from this tab's session storage.
export function readApiKey(): string {
  try { return sessionStorage.getItem(SESSION_KEY) ?? ''; }
  catch { return ''; }
}

export function saveApiKey(value: string): void {
  const key = value.trim();
  if (key) sessionStorage.setItem(SESSION_KEY, key);
  else sessionStorage.removeItem(SESSION_KEY);
}

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function errorDetail(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object' || !('detail' in payload)) return;
  const detail = payload.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((item: unknown) => {
      if (item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string') return item.msg;
      return '';
    }).filter(Boolean).join('; ') || undefined;
  }
}

export function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : 'The request could not be completed. Please try again.';
}

// Requests never follow redirects or send the API key to another origin.
export function createApiClient(getKey = readApiKey, fetcher: typeof fetch = fetch) {
  async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = new Headers(options.headers);
    const key = getKey();
    if (key) headers.set('X-API-Key', key);
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    headers.set('Accept', 'application/json');
    let response: Response;
    try {
      response = await fetcher(`/api${path}`, {
        ...options, headers, credentials: 'same-origin', redirect: 'error',
      });
    } catch (error) {
      if (isAbort(error)) throw error;
      throw new ApiError('Cannot reach the API. Check that the backend is running and retry.', 0);
    }
    if (!response.ok) {
      let detail: string | undefined;
      try { detail = errorDetail(await response.json()); } catch { /* Ignore non-JSON error bodies. */ }
      const fallback = response.status === 401 || response.status === 403
        ? 'API access was denied. Check the X-API-Key in API access settings.'
        : `The API returned HTTP ${response.status}. Please try again.`;
      const message = detail || fallback;
      throw new ApiError(key ? message.split(key).join('[redacted]') : message, response.status);
    }
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; }
    catch { throw new ApiError('The API returned an invalid response. Check the backend and retry.', response.status); }
  }

  return {
    health: (signal?: AbortSignal) => request<Health>('/health', { signal }),
    documents: async (signal?: AbortSignal) => (await request<{ documents: SourceDocument[] }>('/documents', { signal })).documents,
    upload: (file: File, name: string, signal?: AbortSignal) => {
      const body = new FormData();
      body.set('file', file);
      if (name.trim()) body.set('name', name.trim());
      return request<DocumentReceipt>('/documents', { method: 'POST', body, signal });
    },
    paste: (name: string, text: string, signal?: AbortSignal) => request<DocumentReceipt>('/documents/text', {
      method: 'POST', body: JSON.stringify({ name: name.trim(), text }), signal,
    }),
    remove: (id: string, signal?: AbortSignal) => request<void>(`/documents/${encodeURIComponent(id)}`, { method: 'DELETE', signal }),
    retry: (id: string, signal?: AbortSignal) => request<DocumentReceipt>(`/documents/${encodeURIComponent(id)}/retry`, { method: 'POST', signal }),
    job: (id: string, signal?: AbortSignal) => request<Job>(`/jobs/${encodeURIComponent(id)}`, { signal }),
    seed: (signal?: AbortSignal) => request<SeedReceipt>('/demo/seed', { method: 'POST', signal }),
    ask: (input: QueryRequest, signal?: AbortSignal) => request<QueryResult>('/query', {
      method: 'POST', body: JSON.stringify({ ...input, question: input.question.trim() }), signal,
    }),
    history: async (signal?: AbortSignal) => (await request<{ queries: QueryResult[] }>('/queries?limit=20', { signal })).queries,
    graph: (ids: string[], limit: number, signal?: AbortSignal) => {
      const params = new URLSearchParams({ limit: String(limit) });
      if (ids.length) params.set('document_ids', ids.join(','));
      return request<GraphView>(`/graph?${params}`, { signal });
    },
  };
}

export const api = createApiClient();
