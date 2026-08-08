import { useMemo, useState, type ChangeEvent } from 'react';
import { createSession, getDownloadUrl, submitFeedback, type SessionResponse } from './api';

type Stage = 'idle' | 'drafting' | 'review' | 'finalized';

export default function App() {
  const [stage, setStage] = useState<Stage>('idle');
  const [candidateName, setCandidateName] = useState('');
  const [jobDescription, setJobDescription] = useState('');
  const [companyDetails, setCompanyDetails] = useState('');
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [feedback, setFeedback] = useState('');
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const canSubmit = useMemo(() => {
    return Boolean(resumeFile && jobDescription.trim() && companyDetails.trim() && !busy);
  }, [resumeFile, jobDescription, companyDetails, busy]);

  async function handleCreateSession() {
    if (!resumeFile) return;
    setBusy(true);
    setError('');
    try {
      const result = await createSession({
        file: resumeFile,
        jobDescription,
        companyDetails,
        candidateName: candidateName || undefined,
      });
      setSession(result);
      setStage('review');
      setFeedback('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start review');
    } finally {
      setBusy(false);
    }
  }

  async function handleFeedback(approved: boolean) {
    if (!session) return;
    setBusy(true);
    setError('');
    try {
      const result = await submitFeedback(session.session_id, feedback, approved);
      setSession(result);
      setStage(approved ? 'finalized' : 'review');
      if (approved) {
        setFeedback('');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit feedback');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Human-in-the-loop resume optimization</p>
          <h1>Resume ATS Optimizer</h1>
          <p className="subtitle">
            Upload a CV, add the job description and company details, then review each agent draft before approval.
          </p>
        </div>
      </header>

      <main className="grid">
        <section className="panel">
          <h2>1. Upload & Brief</h2>
          <label>
            Candidate name
            <input
              value={candidateName}
              onChange={(event: ChangeEvent<HTMLInputElement>) => setCandidateName(event.target.value)}
              placeholder="Jane Doe"
            />
          </label>
          <label>
            CV file (PDF or Word)
            <input
              type="file"
              accept=".pdf,.doc,.docx"
              onChange={(event: ChangeEvent<HTMLInputElement>) => setResumeFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <label>
            Job description
            <textarea
              value={jobDescription}
              onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setJobDescription(event.target.value)}
              rows={8}
              placeholder="Paste the job description here"
            />
          </label>
          <label>
            Company details
            <textarea
              value={companyDetails}
              onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setCompanyDetails(event.target.value)}
              rows={6}
              placeholder="Company context, values, product, team details"
            />
          </label>
          <button disabled={!canSubmit} onClick={handleCreateSession}>
            {busy && stage === 'idle' ? 'Analyzing...' : 'Generate ATS Draft'}
          </button>
          {error ? <p className="error">{error}</p> : null}
        </section>

        <section className="panel">
          <h2>2. Review Loop</h2>
          {session ? (
            <>
              <div className="metrics">
                <div>
                  <span>Session</span>
                  <strong>{session.session_id.slice(0, 8)}</strong>
                </div>
                <div>
                  <span>ATS Score</span>
                  <strong>{session.ats_score}</strong>
                </div>
                <div>
                  <span>Rounds</span>
                  <strong>{session.feedback_round}</strong>
                </div>
              </div>

              <div className="card">
                <h3>Analysis</h3>
                <p>{session.analysis}</p>
              </div>

              <div className="card">
                <h3>Draft Resume</h3>
                <pre>{session.draft_resume}</pre>
              </div>

              {stage !== 'finalized' ? (
                <>
                  <label>
                    Human feedback
                    <textarea
                      value={feedback}
                      onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setFeedback(event.target.value)}
                      rows={6}
                      placeholder="Tell the agent what to improve before approval"
                    />
                  </label>
                  <div className="button-row">
                    <button className="secondary" onClick={() => handleFeedback(false)} disabled={busy || !feedback.trim()}>
                      {busy ? 'Sending...' : 'Request Revision'}
                    </button>
                    <button onClick={() => handleFeedback(true)} disabled={busy}>
                      Approve & Finalize
                    </button>
                  </div>
                </>
              ) : (
                <div className="card success">
                  <h3>Final Resume Ready</h3>
                  <p>Your final resume is ready for download.</p>
                  <a className="download" href={getDownloadUrl(session.session_id)} target="_blank" rel="noreferrer">
                    Download Final Resume
                  </a>
                </div>
              )}
            </>
          ) : (
            <div className="empty-state">
              <p>Upload a CV and submit the job details to start the human-in-the-loop workflow.</p>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
