import assert from 'node:assert/strict';
import test from 'node:test';
import {
  evidenceAnchor, MAX_TEXT_CHARS, MAX_UPLOAD_BYTES, supportLabels,
  validateFile, validateName, validateQuestion, validateText,
} from '../src/utils.ts';

// Boundary tests protect uploads and contract request limits without a running server.
test('file validation bounds bytes and permits only the four supported extensions', () => {
  for (const name of ['report.PDF', 'notes.txt', 'notes.md', 'report.docx']) {
    assert.equal(validateFile({ name, size: MAX_UPLOAD_BYTES }), null);
  }
  assert.match(validateFile(null), /Choose a file/);
  assert.match(validateFile({ name: 'data.csv', size: 1 }), /PDF, TXT, MD, or DOCX/);
  assert.match(validateFile({ name: 'notes.txt', size: 0 }), /empty/);
  assert.match(validateFile({ name: 'notes.txt', size: MAX_UPLOAD_BYTES + 1 }), /20 MB/);
});

test('paste validation rejects blank and oversized text but allows literal markup', () => {
  assert.match(validateText(' \n\t '), /Paste some/);
  assert.equal(validateText('x'.repeat(MAX_TEXT_CHARS)), null);
  assert.match(validateText('x'.repeat(MAX_TEXT_CHARS + 1)), /500,000/);
  assert.equal(validateText('<img src=x onerror=alert(1)>'), null);
});

test('document names distinguish optional upload names from required paste names', () => {
  assert.equal(validateName('', false), null);
  assert.match(validateName('  '), /document name/);
  assert.match(validateName('../source.txt'), /path separators/);
  assert.match(validateName('notes\u0000.txt'), /control characters/);
  assert.match(validateName('n'.repeat(201)), /200/);
  assert.equal(validateName('Annual report.txt'), null);
});

test('query validation includes trimmed length and the complete top-k and hops boundaries', () => {
  const request = { question: 'What do the sources say?', document_ids: [], top_k: 6, hops: 2 };
  for (const top_k of [2, 20]) for (const hops of [1, 3]) assert.equal(validateQuestion({ ...request, top_k, hops }), null);
  assert.match(validateQuestion({ ...request, question: '  ' }), /Enter a question/);
  assert.equal(validateQuestion({ ...request, question: ` ${'x'.repeat(2000)} ` }), null);
  assert.match(validateQuestion({ ...request, question: 'x'.repeat(2001) }), /2,000/);
  for (const top_k of [1, 21, 2.5, Number.NaN]) assert.match(validateQuestion({ ...request, top_k }), /2 to 20/);
  for (const hops of [0, 4, 1.5]) assert.match(validateQuestion({ ...request, hops }), /1 to 3/);
});

test('source anchors encode arbitrary evidence identifiers', () => {
  assert.equal(evidenceAnchor('doc/1 chunk"<tag>'), 'evidence-doc%2F1%20chunk%22%3Ctag%3E');
});

test('answer statuses describe evidence support rather than probability of truth', () => {
  assert.deepEqual(Object.keys(supportLabels).sort(), ['grounded', 'no_evidence', 'partial', 'ungrounded'].sort());
  assert.equal(supportLabels.grounded, 'Evidence-supported');
  assert.equal(supportLabels.no_evidence, 'No evidence found');
  for (const label of Object.values(supportLabels)) assert.doesNotMatch(label, /certain|guaranteed|true|truth probability/i);
});
