# Architecture

## Principle

The model **suggests**; deterministic code **decides and edits**. The LLM sees
paragraph text and location ids, and returns JSON proposals. It never sees
document XML, never touches the file, and has no tools. Every proposal is
re-verified by code, applied by a deterministic editor only after the user
accepts it, and the result must pass independent fidelity gates before it can be
downloaded.

## Workflow

```
                ┌──────────────────────────── upload (POST /api/sessions) ────────────────────────────┐
 .docx ─► upload_validator ─► artifact store (original, read-only, sha256) ─► docx_parser ─► index (artifact)
                                                                                              │
          ┌─────────────────────────── proposal graph (background task) ───────────────────────┘
          ▼
   request_proposals ──► LLM (1 call, JSON schema, ≤1 re-ask on malformed output)
          │                └► evidence_validator: target, quote, range, evidence, numbers,
          │                   named terms, JD keywords, claim strength, size, links
          ├─(error)─► record_analysis_failure ─► status=analysis_failed (retryable; no fake score)
          └─(ok)────► proposals persisted ─► status=awaiting_review

   user accepts / rejects each edit  (optimistic concurrency on review_revision;
                                      high-risk edits need explicit confirmation)

          ┌─────────────────────────── finalize graph (POST /finalize) ─────────────────────────┐
          ▼                                                                                      │
   load_review (exact accepted set, idempotency key) ─(decisions changed)─► abort (409)          │
          └► apply_edits (docx_editor on a working copy; all-or-nothing)                          │
                 ├─(failed)─► record_finalization: FAIL                                           │
                 └► evaluate_fidelity                                                              │
                      A integrity ─ B structure ─ C content ─ D visual (LibreOffice) ─ E decision │
                 └► record_finalization: PASS | REVIEW_REQUIRED | FAIL ◄────────────────────────┘

   download-link (only PASS, or REVIEW_REQUIRED after acknowledgement, and only for the
   currently accepted set) ─► signed, expiring URL ─► GET /api/downloads/{token}
```

## Modules

| Package | Responsibility |
|---|---|
| `app/core` | Validated env config, structured errors, JSON logging with redaction and correlation ids, session/download tokens |
| `app/document` | Upload validation, OOXML parsing and location index, deterministic editor, fidelity gates (structure, content, render, visual diff, decision) |
| `app/resume` | Prompts (versioned), proposal schemas, evidence validator, edit policy, claim extraction, JD keyword estimate, proposer |
| `app/llm` | `LLMProvider` protocol; Ollama (structured output, bounded retries), disabled, and scripted (tests) providers |
| `app/workflow` | Typed graph state, small nodes, routing, graph construction (LangGraph, or built-in executor when LangGraph is absent) |
| `app/persistence` | SQLite repository (sessions, proposals, decisions, finalizations, audit events) and the artifact store |
| `app/services` | `ResumeService`: every use case and authorization rule, framework-free |
| `app/api` | FastAPI adapter: routes, schemas, error envelope, middleware |
| `app/evaluation` | Fixture corpus, regression injectors, fidelity calibration, proposal evaluation |

## Stable locations

A location id is `loc_` + sha256(document version | part | element path | text
hash). It is bound to the exact uploaded bytes, so a different document, or the
edited output, produces different ids. The editor re-resolves the element path,
re-hashes the paragraph and compares the quoted text immediately before
editing, so stale or ambiguous targets fail instead of being guessed.

## Editing model

Paragraphs are decomposed into segments: `w:t` text (editable unless inside a
field result, tracked change, inline content control or text box), tab/break
characters (never edited), and zero-width objects/markers (images, bookmarks,
comment anchors, field boundaries) that an edit may touch but never cross.

For an accepted edit, a token-level diff between the quoted original and the
replacement decides which characters are kept. Kept characters stay in their
original `w:t`; new characters inherit the formatting of the text they replace.
Only `w:t` text (and `xml:space`) changes. No run, property, relationship or
part is created or removed, and untouched ZIP members are copied byte-for-byte.

## Persistence and concurrency

* SQLite in WAL mode is shared safely by several worker processes on one host.
  Multi-host deployments need a server database behind the same `Repository`
  interface.
* Analysis takes a lease (`claim_analysis`), so concurrent triggers produce one
  model call.
* Decisions require the current `review_revision` (compare-and-set); a second
  tab with stale state gets `409 stale_review`.
* Finalization is keyed by sha256(document version, accepted edit ids and
  texts). Repeating it returns the stored result, and a FAIL for a given set is
  final: retrying cannot produce a different outcome without changing decisions.
* Graph state holds only ids; documents live in the artifact store. Because
  nodes persist their own results, an interrupted run resumes from the
  persisted status without losing decisions. LangGraph checkpointers are not
  required for correctness.

## Security model

| Threat | Control |
|---|---|
| Malicious uploads (ZIP bombs, XXE, traversal, macros, encryption, external templates) | `upload_validator`: streamed size/ratio limits, DOCTYPE rejection and an entity-free parser, member-name checks, content-type checks, external non-hyperlink relationships rejected |
| Prompt injection in resume or JD | Data delimiters that the data cannot close; untrusted-data instruction; no tools; every output re-validated (links, e-mails, unsupported terms and numbers rejected) |
| Fabricated claims | Evidence quotes must exist verbatim; numbers and named terms must be traceable; JD keywords without evidence rejected; outcome or leadership inflation requires explicit confirmation |
| Cross-session access | 256-bit per-session capability token (only its hash is stored); a wrong token is indistinguishable from a missing session (404); artifacts are addressed by server-generated ids |
| Download link leakage | HMAC-signed, 5-minute links, re-checked against the gate and the current accepted set at download time; `Cache-Control: no-store` |
| Shared deployment | `RESUME_DEPLOYMENT_MODE=shared` requires a deployment bearer token and a strong signing secret (validated at startup) and disables `/docs` |
| Data retention | Session TTL (default 24 h) with a periodic purge; `DELETE /api/sessions/{id}` removes all files immediately |
| Log leakage | JSON logs redact content fields; tracebacks are reduced to the exception type; access logs (which contain download tokens) are suppressed |
| External model disclosure | A startup warning when `OLLAMA_BASE_URL` is not local unless `RESUME_LLM_EXTERNAL_PROVIDER_ACKNOWLEDGED=true` |

Per-session tokens isolate sessions; they are not user accounts. Putting this
behind real user identity (SSO) means mapping sessions to users in `authorize`.
