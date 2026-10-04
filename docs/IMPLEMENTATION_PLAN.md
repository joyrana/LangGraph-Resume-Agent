# Implementation Plan and Audit (Phase 1)

Audit of commit `02853cc` ("first commit"), performed before any change.

## What existed

| Area | State found | Decision |
|---|---|---|
| `resume_agent.py` | LangGraph `analyze → draft → finalize`. `draft` asks the LLM to **rewrite the whole resume**; `finalize` calls `create_resume_docx`, which builds a **new blank document** titled "ATS-Optimized Resume". | Replaced. Drafting becomes structured edit proposals; finalization applies accepted edits to the original DOCX. |
| Approval path | `POST /feedback` with `approved=true` re-runs the full graph, so the downloaded file is a **fresh rewrite the user never reviewed**. | Removed. Finalization uses only persisted accepted edits; no model call. |
| `FeedbackRequest` | `feedback: min_length=1`; the UI's Approve button sends `""`, so approval returned HTTP 422. | Removed with the feedback endpoint; decisions no longer need free text. |
| `document_service.py` | Accepts PDF, `.doc` (fed to python-docx → crash). No size, ZIP-bomb or structure checks. Text extraction ignores tables, headers, footers. | Replaced by `app/document/*`. DOCX only. |
| `_analyze_node` | JSON parse failure silently yields `ats_score = 65`. | Removed. Alignment is a deterministic keyword-coverage *estimate*; failures surface as errors. |
| `main.py` | `500` responses return `str(exc)`; downloads trust a stored server path; no auth; deprecated `on_event`. | Replaced by `app/api/*` with error envelope, capability tokens, signed expiring download links, lifespan. |
| `session_store.py` | Process-local dict; lost on restart, inconsistent across workers. | Replaced by SQLite (WAL) repository + artifact store. |
| `llm.py` | Global client never closed; fixed 120 s timeout; no retries; no structured output. | Replaced by provider abstraction with config, bounded retries, JSON-schema output and shutdown hook. |
| `agent.py`, `tools.py`, `cli.py`, `examples.py`, `run-cli.sh` | A generic calculator/search ReAct demo with mocked tools, unrelated to resumes. | Removed (not product functionality; mocked tools could mislead). |
| `mcp_server.py` | Imports `fastmcp.Server`/`Tool`, which do not exist in fastmcp; module cannot import. | Removed (broken). |
| `tests/` | Only cover the calculator demo; `test_agent.py` requires a live Ollama. | Replaced by layered suite. |
| Repo hygiene | 1,392 `frontend/node_modules` files (macOS arm64 binaries), `tsconfig.tsbuildinfo`, and a generated resume in `storage/generated/` committed despite `.gitignore`. | Removed from the index. |
| README | Describes the calculator demo and "API key is already configured". | Rewritten. |
| Docker | `uv` installed to wrong PATH (`/root/.cargo/bin`), whole repo bind-mounted with `--reload`, no LibreOffice. | Rewritten with pinned LibreOffice and non-root user. |

## Target design

```
upload ─► validate (A0) ─► store immutable original ─► parse + location index
      ─► [proposal graph] build_context ─► request_proposals (LLM, 1 call)
                          ─► validate_proposals (deterministic) ─► persist ─► awaiting_review
user accepts / rejects edits (persisted, optimistic concurrency via review revision)
      ─► [finalize graph] load_review ─► apply_edits (deterministic, working copy)
                          ─► evaluate_fidelity (gates A–D) ─► decide (gate E) ─► publish | block
```

Principles: the LLM only returns JSON proposals; every proposal is re-verified
by code; the editor only changes `w:t` text inside the targeted paragraphs and
copies every other ZIP member byte-for-byte; the evaluator is independent of the
editor and blocks delivery on failure.

## DOCX support contract

Editable: paragraph text in the body, tables (including nested tables), block
content controls, headers and footers, including runs inside hyperlinks, where
the edited range consists only of plain `w:t` text.

Read-only but preserved byte-for-byte: text boxes (`w:txbxContent`), field
results, tracked changes, inline content controls, math, comments, footnotes,
endnotes, images, charts, SmartArt, embedded objects.

Rejected at upload: non-`.docx` extensions, macro-enabled packages
(`.docm` content type or `vbaProject.bin`), encrypted (OLE/CFB) files,
malformed ZIP/XML, DTDs, oversize or high-ratio archives, path-traversal
member names.

Not promised: pixel-identical rendering in Microsoft Word. The visual gate uses
LibreOffice as a proxy renderer; see `docs/FIDELITY_EVALUATION.md`.
