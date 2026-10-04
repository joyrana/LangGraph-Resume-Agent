import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import {
  ApiError,
  acknowledge,
  artifactBlobUrl,
  createSession,
  decide,
  deleteSession,
  downloadUrl,
  finalize,
  getFinalization,
  getSession,
  retryAnalysis,
  type Decision,
  type DiffSegment,
  type Finalization,
  type Proposal,
  type SessionView,
} from './api';

const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
const POLL_MS = 1500;
const STORAGE_KEY = 'resume-edit-session';

type Stored = { id: string; token: string };

function loadStored(): Stored | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Stored) : null;
  } catch {
    return null;
  }
}

function saveStored(value: Stored | null) {
  try {
    if (value) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: session lasts for this page only */
  }
}

function message(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return 'Something went wrong. Try again.';
}

export default function App() {
  const [stored, setStored] = useState<Stored | null>(loadStored);
  const [session, setSession] = useState<SessionView | null>(null);
  const [finalization, setFinalization] = useState<Finalization | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const reset = useCallback((note = '') => {
    saveStored(null);
    setStored(null);
    setSession(null);
    setFinalization(null);
    setError(note);
  }, []);

  const handleError = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 404 && err.code === 'not_found') {
        reset('This session has expired or was deleted. Upload your resume again to start over.');
        return;
      }
      setError(message(err));
    },
    [reset],
  );

  // Load and poll the session while analysis runs.
  useEffect(() => {
    if (!stored) return;
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const view = await getSession(stored.id, stored.token);
        if (cancelled) return;
        setSession(view);
        if (view.status === 'analyzing') timer = window.setTimeout(tick, POLL_MS);
      } catch (err) {
        if (!cancelled) handleError(err);
      }
    };
    tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [stored, handleError]);

  // Restore the latest delivery result after a reload.
  useEffect(() => {
    if (!stored || !session?.latest_finalization || finalization) return;
    getFinalization(stored.id, stored.token, session.latest_finalization.finalization_id).then(setFinalization).catch(() => undefined);
  }, [stored, session?.latest_finalization, finalization]);

  async function run<T>(label: string, fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(label);
    setError('');
    try {
      return await fn();
    } catch (err) {
      handleError(err);
      if (err instanceof ApiError && err.code === 'stale_review' && stored) {
        setSession(await getSession(stored.id, stored.token).catch(() => session));
      }
      return undefined;
    } finally {
      setBusy(null);
    }
  }

  async function onCreate(input: { file: File; jobDescription: string; companyDetails: string; candidateNotes: string }) {
    const created = await run('upload', () => createSession(input));
    if (!created) return;
    const next = { id: created.session_id, token: created.session_token };
    saveStored(next);
    setFinalization(null);
    setSession(created.session);
    setStored(next);
  }

  async function onDecide(items: { edit_id: string; decision: Decision; confirm_high_risk?: boolean }[]) {
    if (!stored || !session) return;
    const view = await run('decide', () => decide(stored.id, stored.token, session.review_revision, items));
    if (view) setSession(view);
  }

  async function onFinalize() {
    if (!stored || !session) return;
    const result = await run('finalize', () => finalize(stored.id, stored.token, session.review_revision));
    if (result) {
      setFinalization(result);
      setSession(await getSession(stored.id, stored.token));
    }
  }

  async function onAcknowledge() {
    if (!stored || !finalization) return;
    const result = await run('acknowledge', () => acknowledge(stored.id, stored.token, finalization.finalization_id));
    if (result) setFinalization(result);
  }

  async function onDownload() {
    if (!stored || !finalization) return;
    const url = await run('download', () => downloadUrl(stored.id, stored.token, finalization.finalization_id));
    if (url) window.location.assign(url);
  }

  async function onDelete() {
    if (!stored) return;
    await run('delete', () => deleteSession(stored.id, stored.token));
    reset('Your files for that session were deleted.');
  }

  async function onRetry() {
    if (!stored) return;
    const view = await run('retry', () => retryAnalysis(stored.id, stored.token));
    if (view) {
      setSession(view);
      setStored({ ...stored });
    }
  }

  const step = !session ? 1 : session.status === 'analyzing' || session.status === 'analysis_failed' ? 1 : finalization ? 3 : 2;

  return (
    <div className="shell">
      <header className="masthead">
        <h1>Resume edits, reviewed by you</h1>
        <p>
          Upload your Word resume and a job description. You get small, sourced suggestions to accept or reject. Only the edits you
          accept are written into your original file, and the result is checked for layout changes before you can download it.
        </p>
        <ol className="steps" aria-label="Progress">
          {['Upload', 'Review edits', 'Check and download'].map((label, i) => (
            <li key={label} aria-current={step === i + 1 ? 'step' : undefined} className={step > i + 1 ? 'done' : step === i + 1 ? 'current' : ''}>
              <span className="step-num">{i + 1}</span> {label}
            </li>
          ))}
        </ol>
      </header>

      <div role="alert" aria-live="assertive" className={error ? 'banner error' : 'visually-hidden'}>
        {error}
      </div>

      {!session ? (
        <UploadForm busy={busy === 'upload'} onSubmit={onCreate} />
      ) : (
        <main className="workspace">
          <SessionHeader session={session} onDelete={onDelete} onNew={() => reset()} busy={busy} />
          {session.status === 'analyzing' && (
            <p className="status-line" role="status" aria-live="polite">
              Reading your resume and drafting suggestions. This can take a minute or two with a local model.
            </p>
          )}
          {session.status === 'analysis_failed' && (
            <div className="banner error" role="alert">
              <p>{session.error?.message ?? 'Analysis failed.'}</p>
              <button onClick={onRetry} disabled={busy !== null}>
                Try analysis again
              </button>
            </div>
          )}
          {session.analysis && <AnalysisSummary session={session} />}
          {session.analysis && (
            <Review session={session} busy={busy} onDecide={onDecide} onFinalize={onFinalize} finalization={finalization} />
          )}
          {finalization && stored && (
            <Delivery
              finalization={finalization}
              stored={stored}
              busy={busy}
              onAcknowledge={onAcknowledge}
              onDownload={onDownload}
            />
          )}
        </main>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ upload */

function UploadForm({ busy, onSubmit }: { busy: boolean; onSubmit: (v: { file: File; jobDescription: string; companyDetails: string; candidateNotes: string }) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState('');
  const [jobDescription, setJobDescription] = useState('');
  const [companyDetails, setCompanyDetails] = useState('');
  const [candidateNotes, setCandidateNotes] = useState('');

  function pick(f: File | null) {
    setFileError('');
    setFile(null);
    if (!f) return;
    if (!f.name.toLowerCase().endsWith('.docx')) {
      setFileError('Only Word .docx files are supported. In Word, use File > Save As > Word Document (.docx).');
      return;
    }
    if (f.size > MAX_UPLOAD_BYTES) {
      setFileError(`This file is ${(f.size / 1024 / 1024).toFixed(1)} MB; the limit is 5 MB.`);
      return;
    }
    setFile(f);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (file && jobDescription.trim()) onSubmit({ file, jobDescription, companyDetails, candidateNotes });
  }

  return (
    <form className="upload" onSubmit={submit}>
      <div className="field">
        <label htmlFor="file">Your resume (.docx)</label>
        <input
          id="file"
          type="file"
          accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          onChange={(e) => pick(e.target.files?.[0] ?? null)}
          aria-describedby="file-help"
          aria-invalid={Boolean(fileError)}
        />
        <p id="file-help" className={fileError ? 'help error-text' : 'help'}>
          {fileError || 'PDF and older .doc files are not accepted. Your original file is never changed.'}
        </p>
      </div>
      <div className="field">
        <label htmlFor="jd">Job description</label>
        <textarea id="jd" rows={9} maxLength={20000} value={jobDescription} onChange={(e) => setJobDescription(e.target.value)} required />
      </div>
      <div className="field">
        <label htmlFor="company">Company details (optional)</label>
        <textarea id="company" rows={3} maxLength={5000} value={companyDetails} onChange={(e) => setCompanyDetails(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="notes">Facts you can confirm (optional)</label>
        <textarea
          id="notes"
          rows={3}
          maxLength={5000}
          value={candidateNotes}
          onChange={(e) => setCandidateNotes(e.target.value)}
          aria-describedby="notes-help"
        />
        <p id="notes-help" className="help">
          Numbers or details that are true but missing from the resume, such as "the migration cut hosting costs by 20%". Suggestions may
          only add facts that appear in your resume or here.
        </p>
      </div>
      <button type="submit" className="primary" disabled={!file || !jobDescription.trim() || busy}>
        {busy ? 'Uploading…' : 'Check my resume'}
      </button>
    </form>
  );
}

/* ------------------------------------------------------------------ session */

function SessionHeader({ session, onDelete, onNew, busy }: { session: SessionView; onDelete: () => void; onNew: () => void; busy: string | null }) {
  const doc = session.document;
  return (
    <section className="doc-header" aria-label="Document">
      <div>
        <h2>{doc.file_name}</h2>
        <p className="help">
          {doc.editable_paragraphs} of {doc.paragraphs} paragraphs can be edited. Files are deleted automatically on{' '}
          {new Date(session.expires_at).toLocaleString()}.
        </p>
        {doc.limitations.length > 0 && (
          <details className="limits">
            <summary>Parts of this document that stay exactly as they are ({doc.limitations.length})</summary>
            <ul>
              {doc.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </details>
        )}
      </div>
      <div className="row">
        <button onClick={onNew} disabled={busy !== null}>
          Start over
        </button>
        <button className="quiet" onClick={onDelete} disabled={busy !== null}>
          Delete my files
        </button>
      </div>
    </section>
  );
}

function AnalysisSummary({ session }: { session: SessionView }) {
  const a = session.analysis!;
  const est = a.alignment_estimate;
  return (
    <section className="analysis" aria-label="Job match">
      <div className="coverage">
        {est.coverage_percent !== null ? (
          <p>
            <strong>{est.coverage_percent}%</strong> of the job's key terms already appear in your resume.
          </p>
        ) : (
          <p>No key terms could be extracted from the job description.</p>
        )}
        <p className="help">{est.label}.</p>
      </div>
      {a.alignment_summary && <p>{a.alignment_summary}</p>}
      {a.gaps_without_evidence.length > 0 && (
        <div>
          <h3>Asked for in the job, not shown in your resume</h3>
          <p className="help">No edits add these. If they are true, add them yourself or list them under facts you can confirm.</p>
          <ul className="chips">
            {a.gaps_without_evidence.map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        </div>
      )}
      {a.rejected_proposal_count > 0 && (
        <p className="help">
          {a.rejected_proposal_count} suggestion{a.rejected_proposal_count === 1 ? ' was' : 's were'} discarded automatically because they
          could not be traced to your resume or could not be applied safely.
        </p>
      )}
      {a.context_truncated && <p className="help">Your resume is long, so only its first part was reviewed.</p>}
    </section>
  );
}

/* ------------------------------------------------------------------ review */

function Review({
  session,
  busy,
  onDecide,
  onFinalize,
  finalization,
}: {
  session: SessionView;
  busy: string | null;
  onDecide: (items: { edit_id: string; decision: Decision; confirm_high_risk?: boolean }[]) => void;
  onFinalize: () => void;
  finalization: Finalization | null;
}) {
  const proposals = session.proposals;
  const accepted = session.counts.accepted ?? 0;
  const pendingLowRisk = proposals.filter((p) => p.decision === 'pending' && p.risk_level === 'low');
  const pending = proposals.filter((p) => p.decision === 'pending');
  const locked = busy !== null || session.status === 'finalizing';

  if (proposals.length === 0) {
    return (
      <section className="review">
        <h2>Suggested edits</h2>
        <p>No safe edits were found. Your resume already covers what the model could support with evidence.</p>
      </section>
    );
  }

  return (
    <section className="review" aria-label="Suggested edits">
      <div className="review-head">
        <h2>Suggested edits</h2>
        <p className="help">
          {accepted} accepted, {session.counts.rejected ?? 0} rejected, {pending.length} undecided
        </p>
        <div className="row">
          <button disabled={locked || pendingLowRisk.length === 0} onClick={() => onDecide(pendingLowRisk.map((p) => ({ edit_id: p.edit_id, decision: 'accepted' })))}>
            Accept all low-risk ({pendingLowRisk.length})
          </button>
          <button disabled={locked || pending.length === 0} onClick={() => onDecide(pending.map((p) => ({ edit_id: p.edit_id, decision: 'rejected' })))}>
            Reject undecided ({pending.length})
          </button>
        </div>
      </div>

      <ol className="proposals">
        {proposals.map((p) => (
          <ProposalItem key={p.edit_id} p={p} disabled={locked} onDecide={onDecide} />
        ))}
      </ol>

      <div className="finalize">
        {finalization && !finalization.is_current && (
          <p className="help">You changed decisions after the last check. Apply edits again to get a new file.</p>
        )}
        <button className="primary" disabled={locked || accepted === 0 || Boolean(finalization?.is_current)} onClick={onFinalize}>
          {busy === 'finalize'
            ? 'Applying and checking layout…'
            : finalization?.is_current
              ? 'Accepted edits applied'
              : `Apply ${accepted} accepted edit${accepted === 1 ? '' : 's'} to my file`}
        </button>
        {accepted === 0 && <p className="help">Accept at least one edit to continue.</p>}
      </div>
    </section>
  );
}

function Diff({ segments }: { segments: DiffSegment[] }) {
  return (
    <>
      {segments.map((s, i) =>
        s.op === 'equal' ? (
          <span key={i}>{s.text}</span>
        ) : s.op === 'insert' ? (
          <ins key={i}>{s.text}</ins>
        ) : (
          <del key={i}>{s.text}</del>
        ),
      )}
    </>
  );
}

const TYPE_LABEL: Record<string, string> = {
  grammar: 'Grammar',
  clarity: 'Clarity',
  impact: 'Impact',
  keyword_alignment: 'Job keywords',
  concision: 'Shorter',
};

function ProposalItem({ p, disabled, onDecide }: { p: Proposal; disabled: boolean; onDecide: (items: { edit_id: string; decision: Decision; confirm_high_risk?: boolean }[]) => void }) {
  const [confirm, setConfirm] = useState(p.confirmed);
  const before = p.paragraph_text.slice(0, p.start);
  const after = p.paragraph_text.slice(p.end);
  const needsConfirm = p.requires_user_confirmation;
  return (
    <li className={`proposal ${p.decision}`} aria-label={`Edit in ${p.section ?? 'resume'}`}>
      <div className="page">
        <p className="where">
          {p.section ?? 'Resume'}
          {p.container !== 'body' ? ` (${p.container.replace('_', ' ')})` : ''}
        </p>
        <p className="doc-text">
          {before}
          {p.decision === 'accepted' ? <span className="applied">{p.proposed_text}</span> : <Diff segments={p.diff} />}
          {after}
        </p>
        {p.decision === 'rejected' && <p className="help">Rejected: this text stays as it is.</p>}
      </div>
      <aside className="margin" aria-label="Why this edit">
        <p className="kind">
          {TYPE_LABEL[p.edit_type] ?? p.edit_type}
          {p.risk_level !== 'low' && <span className={`risk ${p.risk_level}`}>{p.risk_level === 'high' ? 'Check carefully' : 'Review'}</span>}
        </p>
        <p>{p.reason}</p>
        <details>
          <summary>Based on</summary>
          <ul className="evidence">
            {p.supporting_evidence.map((e) => (
              <li key={e.location_id + e.quote}>
                <q>{e.quote}</q>
                {e.section ? <span className="help"> in {e.section}</span> : null}
              </li>
            ))}
          </ul>
        </details>
        {p.validation_warnings.length > 0 && (
          <ul className="warnings">
            {p.validation_warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}
        {needsConfirm && p.decision !== 'accepted' && (
          <label className="confirm">
            <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} disabled={disabled} />
            This claim is accurate
          </label>
        )}
        <div className="row" role="group" aria-label="Decision">
          <button
            aria-pressed={p.decision === 'accepted'}
            className={p.decision === 'accepted' ? 'accept on' : 'accept'}
            disabled={disabled || (needsConfirm && !confirm && p.decision !== 'accepted')}
            onClick={() => onDecide([{ edit_id: p.edit_id, decision: p.decision === 'accepted' ? 'pending' : 'accepted', confirm_high_risk: confirm }])}
          >
            {p.decision === 'accepted' ? 'Accepted' : 'Accept'}
          </button>
          <button
            aria-pressed={p.decision === 'rejected'}
            className={p.decision === 'rejected' ? 'reject on' : 'reject'}
            disabled={disabled}
            onClick={() => onDecide([{ edit_id: p.edit_id, decision: p.decision === 'rejected' ? 'pending' : 'rejected' }])}
          >
            {p.decision === 'rejected' ? 'Rejected' : 'Reject'}
          </button>
        </div>
      </aside>
    </li>
  );
}

/* ------------------------------------------------------------------ delivery */

const GATE_LABEL: Record<string, string> = {
  integrity: 'File opens and is intact',
  structure: 'Formatting and structure unchanged',
  content: 'Only your accepted edits applied',
  visual: 'Rendered layout compared',
};

const STATUS_LABEL: Record<string, string> = { PASS: 'Passed', REVIEW_REQUIRED: 'Needs your review', FAIL: 'Failed', NOT_RUN: 'Not run' };

function Delivery({
  finalization,
  stored,
  busy,
  onAcknowledge,
  onDownload,
}: {
  finalization: Finalization;
  stored: { id: string; token: string };
  busy: string | null;
  onAcknowledge: () => void;
  onDownload: () => void;
}) {
  const report = finalization.report;
  const [ack, setAck] = useState(false);
  const [images, setImages] = useState<Record<string, string>>({});
  const urls = useRef<string[]>([]);

  useEffect(() => () => urls.current.forEach((u) => URL.revokeObjectURL(u)), []);

  async function showComparisons() {
    if (!report) return;
    const loaded: Record<string, string> = {};
    for (const name of Object.keys(report.artifacts)) {
      try {
        const url = await artifactBlobUrl(stored.id, stored.token, finalization.finalization_id, name);
        urls.current.push(url);
        loaded[name] = url;
      } catch {
        /* skip unavailable image */
      }
    }
    setImages(loaded);
  }

  const headline = useMemo(() => {
    if (finalization.decision === 'PASS') return 'Your edited resume passed every check.';
    if (finalization.decision === 'REVIEW_REQUIRED') return 'Your edits were applied, but the layout changed in ways you should look at.';
    return 'The edited file did not pass the checks, so it cannot be downloaded.';
  }, [finalization.decision]);

  return (
    <section className={`delivery ${finalization.decision.toLowerCase()}`} aria-label="Result" aria-live="polite">
      <h2>{headline}</h2>
      {finalization.edit_failures.length > 0 && (
        <ul className="warnings">
          {finalization.edit_failures.map((f) => (
            <li key={f.edit_id + f.code}>{f.message}</li>
          ))}
        </ul>
      )}
      {report && (
        <>
          <ul className="gates">
            {Object.entries(report.gates).map(([name, gate]) => (
              <li key={name} className={gate.status.toLowerCase()}>
                <span>{GATE_LABEL[name] ?? name}</span>
                <span>{STATUS_LABEL[gate.status]}</span>
              </li>
            ))}
          </ul>
          {report.page_count.original !== null && (
            <p className="help">
              Pages: {report.page_count.original} before, {report.page_count.output} after. Layout was compared using LibreOffice{' '}
              {report.renderer.version ?? ''}; Word may lay text out slightly differently.
            </p>
          )}
          {finalization.decision !== 'PASS' && report.reasons.length > 0 && (
            <ul className="warnings">
              {report.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
          {Object.keys(report.artifacts).length > 0 && Object.keys(images).length === 0 && (
            <button onClick={showComparisons}>Show where the layout changed</button>
          )}
          {Object.entries(images).map(([name, url]) => (
            <figure key={name}>
              <img src={url} alt={`Original and edited ${name.replace(/_/g, ' ')}, with unexpected changes outlined in red`} />
              <figcaption>Left: original. Right: edited. Red boxes mark changes outside your accepted edits.</figcaption>
            </figure>
          ))}
        </>
      )}

      {finalization.decision === 'REVIEW_REQUIRED' && !finalization.acknowledged && finalization.is_current && (
        <div className="ack">
          <label>
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            I have read these warnings and will check the layout of the downloaded file in Word.
          </label>
          <button disabled={!ack || busy !== null} onClick={onAcknowledge}>
            Continue to download
          </button>
        </div>
      )}

      {finalization.downloadable ? (
        <button className="primary" onClick={onDownload} disabled={busy !== null}>
          Download edited resume (.docx)
        </button>
      ) : (
        finalization.blocked_reason && <p className="help">{finalization.blocked_reason}</p>
      )}
    </section>
  );
}
