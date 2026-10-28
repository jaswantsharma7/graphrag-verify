import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiError, createApiClient, readApiKey, saveApiKey } from '../src/api.ts';

// Network fixtures are test-only; the running interface always reads the real API.
function recorder(payload, status = 200, key = '') {
  const calls = [];
  const client = createApiClient(() => key, async (url, options) => {
    calls.push({ url, options });
    return new Response(status === 204 ? null : JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
  });
  return { client, calls };
}

test('question requests preserve scope and retrieval settings and trim the question', async () => {
  const { client, calls } = recorder({ id: 'query-fixture' }, 200, 'test-only-key');
  await client.ask({ question: '  What is stated?  ', document_ids: ['doc-a'], top_k: 8, hops: 3 });
  const { url, options } = calls[0];
  assert.equal(url, '/api/query');
  assert.equal(options.method, 'POST');
  assert.deepEqual(JSON.parse(options.body), { question: 'What is stated?', document_ids: ['doc-a'], top_k: 8, hops: 3 });
  assert.equal(options.headers.get('X-API-Key'), 'test-only-key');
  assert.equal(options.headers.get('Content-Type'), 'application/json');
  assert.equal(options.credentials, 'same-origin');
  assert.equal(options.redirect, 'error');
});

test('multipart uploads leave the boundary to fetch and include the optional name', async () => {
  const { client, calls } = recorder({ document: {}, job: {} }, 202);
  await client.upload(new File(['sample source'], 'report.PDF', { type: 'application/pdf' }), '  Report  ');
  const { url, options } = calls[0];
  assert.equal(url, '/api/documents');
  assert.equal(options.headers.has('Content-Type'), false);
  assert.equal(options.headers.has('X-API-Key'), false);
  assert.equal(options.body.get('file').name, 'report.PDF');
  assert.equal(options.body.get('name'), 'Report');
});

test('paste requests preserve source text rather than interpreting markup', async () => {
  const { client, calls } = recorder({ document: {}, job: {} }, 202);
  const text = '  <script>not executable source text</script>\n';
  await client.paste('  Notes  ', text);
  assert.equal(calls[0].url, '/api/documents/text');
  assert.deepEqual(JSON.parse(calls[0].options.body), { name: 'Notes', text });
});

test('graph requests use comma-separated document filters and omit an all-documents filter', async () => {
  const { client, calls } = recorder({ nodes: [], edges: [] });
  await client.graph(['doc one', 'doc-two'], 50);
  await client.graph([], 100);
  const first = new URL(calls[0].url, 'http://test.invalid');
  assert.equal(first.pathname, '/api/graph');
  assert.equal(first.searchParams.get('document_ids'), 'doc one,doc-two');
  assert.equal(first.searchParams.get('limit'), '50');
  assert.equal(new URL(calls[1].url, 'http://test.invalid').searchParams.has('document_ids'), false);
});

test('document and job identifiers are encoded as path segments', async () => {
  const { client, calls } = recorder({});
  await client.retry('doc/with spaces');
  await client.job('job/with spaces');
  assert.equal(calls[0].url, '/api/documents/doc%2Fwith%20spaces/retry');
  assert.equal(calls[1].url, '/api/jobs/job%2Fwith%20spaces');
});

test('deletion handles the contract 204 without parsing a body', async () => {
  const { client, calls } = recorder(null, 204);
  assert.equal(await client.remove('doc-a'), undefined);
  assert.equal(calls[0].options.method, 'DELETE');
});

test('document and query lists unwrap their transport envelopes', async () => {
  const documents = [{ id: 'doc-fixture' }];
  const queries = [{ id: 'query-fixture' }];
  const documentClient = recorder({ documents });
  const queryClient = recorder({ queries });
  assert.deepEqual(await documentClient.client.documents(), documents);
  assert.deepEqual(await queryClient.client.history(), queries);
  assert.equal(queryClient.calls[0].url, '/api/queries?limit=20');
});

test('samples are seeded only through the explicit seed method', async () => {
  const { client, calls } = recorder({ documents: [], jobs: [] });
  await client.health();
  await client.documents();
  assert.equal(calls.some((call) => call.url === '/api/demo/seed'), false);
  await client.seed();
  assert.equal(calls[2].url, '/api/demo/seed');
  assert.equal(calls[2].options.method, 'POST');
});

test('readable backend errors retain HTTP status and redact an echoed API key', async () => {
  const { client } = recorder({ detail: 'Invalid test-only-key credential' }, 401, 'test-only-key');
  await assert.rejects(client.health(), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 401);
    assert.equal(error.message, 'Invalid [redacted] credential');
    return true;
  });
});

test('validation errors display messages without exposing submitted inputs', async () => {
  const { client } = recorder({ detail: [{ msg: 'Document is too large', input: 'private source material' }] }, 422);
  await assert.rejects(client.documents(), /Document is too large/);
});

test('non-JSON failures use actionable fallback text and no body contents', async () => {
  const client = createApiClient(() => '', async () => new Response('<html>internal traceback</html>', { status: 503 }));
  await assert.rejects(client.health(), (error) => error.status === 503 && !error.message.includes('traceback'));
});

test('network errors are bounded, while request cancellation remains cancellation', async () => {
  const failed = createApiClient(() => '', async () => { throw new TypeError('private network detail'); });
  await assert.rejects(failed.health(), (error) => error.status === 0 && error.message.includes('backend is running'));
  const aborted = createApiClient(() => '', async () => { throw new DOMException('Aborted', 'AbortError'); });
  await assert.rejects(aborted.health(), (error) => error.name === 'AbortError');
});

test('API keys are saved, read, and cleared only through sessionStorage', () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
  const values = new Map();
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  } });
  try {
    saveApiKey('  test-only-key  ');
    assert.equal(readApiKey(), 'test-only-key');
    assert.deepEqual([...values.keys()], ['graphrag-verify.api-key']);
    saveApiKey('');
    assert.equal(readApiKey(), '');
    assert.equal(values.size, 0);
  } finally {
    if (previous) Object.defineProperty(globalThis, 'sessionStorage', previous);
    else delete globalThis.sessionStorage;
  }
});
