export type SessionResponse = {
  session_id: string;
  status: string;
  ats_score: number;
  analysis: string;
  draft_resume: string;
  candidate_name?: string | null;
  job_description: string;
  company_details: string;
  feedback_round: number;
  final_download_url?: string | null;
  original_file_name: string;
  created_at: string;
  updated_at: string;
  feedback_history: Array<Record<string, unknown>>;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${input.toString()}`, init);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function createSession(payload: {
  file: File;
  jobDescription: string;
  companyDetails: string;
  candidateName?: string;
}): Promise<SessionResponse> {
  const formData = new FormData();
  formData.append('file', payload.file);
  formData.append('job_description', payload.jobDescription);
  formData.append('company_details', payload.companyDetails);
  if (payload.candidateName) {
    formData.append('candidate_name', payload.candidateName);
  }

  const response = await fetch(`${API_BASE_URL}/api/sessions`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<SessionResponse>;
}

export async function submitFeedback(
  sessionId: string,
  feedback: string,
  approved: boolean,
): Promise<SessionResponse> {
  return requestJson<SessionResponse>(`/api/sessions/${sessionId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ feedback, approved }),
  });
}

export async function getSession(sessionId: string): Promise<SessionResponse> {
  return requestJson<SessionResponse>(`/api/sessions/${sessionId}`);
}

export function getDownloadUrl(sessionId: string): string {
  return `${API_BASE_URL}/api/sessions/${sessionId}/download`;
}
