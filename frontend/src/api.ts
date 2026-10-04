export type Decision = 'pending' | 'accepted' | 'rejected';
export type GateStatus = 'PASS' | 'REVIEW_REQUIRED' | 'FAIL' | 'NOT_RUN';

export type DiffSegment = { op: 'equal' | 'insert' | 'delete'; text: string };

export type Proposal = {
  edit_id: string;
  location_id: string;
  section: string | null;
  container: string;
  original_text: string;
  proposed_text: string;
  paragraph_text: string;
  paragraph_after: string;
  start: number;
  end: number;
  edit_type: string;
  reason: string;
  supporting_evidence: { location_id: string; quote: string; section: string | null }[];
  model_confidence: number;
  risk_level: 'low' | 'medium' | 'high';
  requires_user_confirmation: boolean;
  validation_warnings: string[];
  decision: Decision;
  confirmed: boolean;
  diff: DiffSegment[];
};

export type Finding = { code: string; severity: 'info' | 'warning' | 'error'; message: string };
export type Gate = { name: string; status: GateStatus; findings: Finding[]; metrics: Record<string, unknown> };

export type FidelityReport = {
  decision: GateStatus;
  reasons: string[];
  gates: Record<string, Gate>;
  accepted_edit_verification: { edit_id: string; status: string; message?: string | null }[];
  unexpected_change_count: number;
  page_count: { original: number | null; output: number | null };
  layout_warnings: string[];
  unsupported_features: string[];
  renderer: { version: string | null; pinned: boolean };
  artifacts: Record<string, string>;
};

export type Finalization = {
  finalization_id: string;
  decision: 'PASS' | 'REVIEW_REQUIRED' | 'FAIL';
  created_at: string;
  review_revision: number;
  is_current: boolean;
  acknowledged: boolean;
  downloadable: boolean;
  blocked_reason: string | null;
  edit_failures: { edit_id: string; code: string; message: string }[];
  report: FidelityReport | null;
};

export type SessionView = {
  session_id: string;
  status: 'analyzing' | 'analysis_failed' | 'awaiting_review' | 'finalizing' | 'finalized';
  expires_at: string;
  review_revision: number;
  document: {
    file_name: string;
    paragraphs: number;
    editable_paragraphs: number;
    limitations: string[];
    upload_warnings: string[];
  };
  analysis: {
    alignment_summary: string;
    gaps_without_evidence: string[];
    alignment_estimate: { label: string; coverage_percent: number | null; matched_terms: string[]; missing_terms: string[] };
    rejected_proposal_count: number;
    rejected_reasons: Record<string, number>;
    context_truncated: boolean;
    model: Record<string, unknown>;
  } | null;
  proposals: Proposal[];
  counts: Record<string, number>;
  error: { code: string; message: string } | null;
  latest_finalization: Finalization | null;
};

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: Record<string, unknown> = {}) {
    super(message);
  }
}

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '';
const TIMEOUT_MS = 120_000;
const FINALIZE_TIMEOUT_MS = 600_000;

async function request<T>(path: string, init: RequestInit & { token?: string; timeoutMs?: number } = {}): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), init.timeoutMs ?? TIMEOUT_MS);
  const headers = new Headers(init.headers);
  if (init.token) headers.set('X-Session-Token', init.token);
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers, signal: controller.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError(0, 'timeout', 'The server took too long to respond. Your decisions are saved; try again.');
    }
    throw new ApiError(0, 'network', 'Could not reach the server. Check that the backend is running.');
  } finally {
    window.clearTimeout(timer);
  }
  if (!response.ok) {
    let code = 'http_error';
    let message = `Request failed (${response.status}).`;
    let details: Record<string, unknown> = {};
    try {
      const body = await response.json();
      code = body?.error?.code ?? code;
      message = body?.error?.message ?? message;
      details = body?.error?.details ?? {};
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, code, message, details);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function createSession(input: { file: File; jobDescription: string; companyDetails: string; candidateNotes: string }) {
  const form = new FormData();
  form.append('file', input.file);
  form.append('job_description', input.jobDescription);
  form.append('company_details', input.companyDetails);
  form.append('candidate_notes', input.candidateNotes);
  return request<{ session_id: string; session_token: string; session: SessionView }>('/api/sessions', { method: 'POST', body: form });
}

export const getSession = (id: string, token: string) => request<SessionView>(`/api/sessions/${id}`, { token });

export const retryAnalysis = (id: string, token: string) => request<SessionView>(`/api/sessions/${id}/analysis`, { method: 'POST', token });

export function decide(id: string, token: string, revision: number, items: { edit_id: string; decision: Decision; confirm_high_risk?: boolean }[]) {
  return request<SessionView>(`/api/sessions/${id}/decisions`, {
    method: 'POST',
    token,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ review_revision: revision, decisions: items.map((i) => ({ confirm_high_risk: false, ...i })) }),
  });
}

export const finalize = (id: string, token: string, revision: number) =>
  request<Finalization>(`/api/sessions/${id}/finalize`, {
    method: 'POST',
    token,
    timeoutMs: FINALIZE_TIMEOUT_MS,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ review_revision: revision }),
  });

export const getFinalization = (id: string, token: string, fid: string) => request<Finalization>(`/api/sessions/${id}/finalizations/${fid}`, { token });

export const acknowledge = (id: string, token: string, fid: string) =>
  request<Finalization>(`/api/sessions/${id}/finalizations/${fid}/acknowledge`, { method: 'POST', token });

export async function downloadUrl(id: string, token: string, fid: string): Promise<string> {
  const link = await request<{ url: string; expires_at: number }>(`/api/sessions/${id}/finalizations/${fid}/download-link`, { method: 'POST', token });
  return `${API_BASE_URL}${link.url}`;
}

export async function artifactBlobUrl(id: string, token: string, fid: string, name: string): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/api/sessions/${id}/finalizations/${fid}/artifacts/${name}`, { headers: { 'X-Session-Token': token } });
  if (!response.ok) throw new ApiError(response.status, 'artifact', 'Comparison image unavailable.');
  return URL.createObjectURL(await response.blob());
}

export const deleteSession = (id: string, token: string) => request<void>(`/api/sessions/${id}`, { method: 'DELETE', token });
