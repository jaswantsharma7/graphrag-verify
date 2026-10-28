import type { DocumentStatus, QueryRequest, SupportStatus } from './types.ts';

// Frontend bounds match the default server limits; stricter server limits still apply.
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
export const MAX_TEXT_CHARS = 500_000;
export const MAX_NAME_CHARS = 200;
export const ACCEPTED_FILES = '.pdf,.txt,.md,.docx';

export const documentLabels: Record<DocumentStatus, string> = {
  queued: 'Queued', processing: 'Processing', ready: 'Indexed', failed: 'Failed',
};

export const supportLabels: Record<SupportStatus, string> = {
  grounded: 'Evidence-supported', partial: 'Partially supported',
  ungrounded: 'Insufficient support', no_evidence: 'No evidence found',
};

export function validateFile(file: Pick<File, 'name' | 'size'> | null): string | null {
  if (!file) return 'Choose a file to upload.';
  if (!/\.(pdf|txt|md|docx)$/i.test(file.name)) return 'Choose a PDF, TXT, MD, or DOCX file.';
  if (file.size <= 0) return 'The selected file is empty.';
  if (file.size > MAX_UPLOAD_BYTES) return 'The selected file exceeds the 20 MB upload limit.';
  return null;
}

export function validateName(name: string, required = true): string | null {
  if (required && !name.trim()) return 'Enter a document name.';
  if (name.trim().length > MAX_NAME_CHARS) return `Use a document name of ${MAX_NAME_CHARS} characters or fewer.`;
  if (/[\u0000-\u001f\u007f\\/]/.test(name)) return 'Use a document name without path separators or control characters.';
  return null;
}

export function validateText(text: string): string | null {
  if (!text.trim()) return 'Paste some document text to index.';
  if (text.length > MAX_TEXT_CHARS) return 'Pasted text exceeds the 500,000-character limit.';
  return null;
}

export function validateQuestion(input: QueryRequest): string | null {
  if (!input.question.trim()) return 'Enter a question about your documents.';
  if (input.question.trim().length > 2000) return 'Keep the question to 2,000 characters or fewer.';
  if (!Number.isInteger(input.top_k) || input.top_k < 2 || input.top_k > 20) return 'Evidence count must be an integer from 2 to 20.';
  if (!Number.isInteger(input.hops) || input.hops < 1 || input.hops > 3) return 'Graph hops must be an integer from 1 to 3.';
  return null;
}

export function percent(value: number): string {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

export function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Date unavailable' : date.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

export function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function evidenceAnchor(id: string): string {
  return `evidence-${encodeURIComponent(id)}`;
}
