# HTTP API

Interactive docs: `http://localhost:8000/docs` (local mode only).

Every error uses one envelope:

```json
{"error": {"code": "stale_review", "message": "The review changed since it was loaded. Reload the proposals and try again.", "details": {}, "correlation_id": "4f0c…"}}
```

Authentication:

* `X-Session-Token: <token>` on every `/api/sessions/{id}/…` call. The token is
  returned once, when the session is created.
* In `shared` mode, also `Authorization: Bearer <RESUME_API_AUTH_TOKEN>` on all
  `/api/*` routes except `/api/downloads/{token}`.

| Method & path | Purpose | Success | Notable errors |
|---|---|---|---|
| `GET /health` | Liveness | 200 | |
| `GET /ready` | Database, renderer (version, pinned) and LLM config | 200 / 503 | |
| `POST /api/sessions` (multipart: `file`, `job_description`, `company_details?`, `candidate_notes?`) | Validate and store the DOCX, start analysis in the background | 202 `SessionCreated` | 413 `payload_too_large`, 422 `upload_rejected` (with `details.reason`), 422 `input_too_long` |
| `GET /api/sessions/{id}` | Status, document summary, analysis, proposals with diffs, counts, latest result | 200 `SessionView` | 404 |
| `POST /api/sessions/{id}/analysis` | Retry after `analysis_failed` | 202 | 409 `invalid_state` |
| `PUT /api/sessions/{id}/proposals/{edit_id}/decision` `{review_revision, decision, confirm_high_risk}` | Accept, reject or reset one edit | 200 `SessionView` | 409 `stale_review`, 409 `invalid_state` (`details.reason=confirmation_required`) |
| `POST /api/sessions/{id}/decisions` `{review_revision, decisions:[…]}` | Bulk decisions (atomic) | 200 | as above |
| `POST /api/sessions/{id}/finalize` `{review_revision}` | Apply exactly the accepted edits and run the fidelity gates. Idempotent per accepted set; never calls the model | 200 `FinalizationView` | 409 `stale_review`, 409 `invalid_state` (`no_accepted_edits`, already running) |
| `GET /api/sessions/{id}/finalizations/{fid}` | Result with full `FidelityReport` | 200 | 404 |
| `POST /api/sessions/{id}/finalizations/{fid}/acknowledge` | Accept the warnings of a `REVIEW_REQUIRED` result (not possible for `FAIL`) | 200 | 409 |
| `POST /api/sessions/{id}/finalizations/{fid}/download-link` | Short-lived signed URL | 200 `DownloadLink` | 409 `delivery_blocked` |
| `GET /api/sessions/{id}/finalizations/{fid}/artifacts/visual_diff_page_N` | Side-by-side PNG with unexpected regions outlined | 200 image/png | 404 |
| `GET /api/downloads/{token}` | The edited DOCX | 200 | 401 invalid/expired, 409 `delivery_blocked` |
| `GET /api/sessions/{id}/events` | Audit trail (no content) | 200 | |
| `DELETE /api/sessions/{id}` | Delete all files and records now | 204 | |

## Breaking changes from 0.2

* `POST /api/sessions` returns **202** with `session_token` and a `SessionView`.
  There is no `ats_score` or `draft_resume`: the system no longer generates a
  replacement resume. A clearly labelled keyword-coverage estimate is in
  `analysis.alignment_estimate`.
* `POST /api/sessions/{id}/feedback` is removed. Review happens per edit
  (`…/decision`, `…/decisions`) and delivery through `…/finalize`. Approval no
  longer needs feedback text (the old endpoint returned 422 for empty feedback).
* `GET /api/sessions/{id}/download` is replaced by signed download links.
* `candidate_name` is no longer accepted; the name is never sent to the model
  separately.
* `.pdf` and `.doc` uploads are rejected.
