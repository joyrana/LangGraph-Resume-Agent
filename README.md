# LangGraph Resume Agent

Improves a Word resume for a specific job **without replacing it**. You upload
your `.docx` and a job description. The system proposes small, sourced text
edits; you accept or reject each one; only the accepted edits are written into
your original file. The result is checked for structural, content and layout
changes before you can download it.

* **DOCX only.** PDF, `.doc`, RTF, ODT, templates, macro-enabled, encrypted or
  malformed files are rejected with a specific reason.
* **The model suggests; code edits.** The LLM returns JSON proposals. It never
  sees or writes document XML. A deterministic editor changes only the text of
  the targeted runs, so fonts, styles, bullets, tables, headers, images and page
  setup stay exactly as they were.
* **No invented facts.** Every proposal must quote the resume as evidence.
  New numbers, technologies, certifications or job-description keywords that
  the resume does not support are rejected. Edits that inflate claims ("worked
  on" → "led") need your explicit confirmation.
* **Delivery gates.** Every output is reopened and compared with the original
  (package integrity, XML structure, exact accepted text, rendered layout via
  LibreOffice). Failures block the download; layout warnings require your
  acknowledgement.

Documentation: [Architecture](docs/ARCHITECTURE.md) · [HTTP API](docs/API.md) ·
[Fidelity evaluation and limitations](docs/FIDELITY_EVALUATION.md) ·
[Audit and plan](docs/IMPLEMENTATION_PLAN.md)

## Requirements

| Component | Version | Why |
|---|---|---|
| Python | 3.11–3.13 | backend |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.5 | dependency management |
| LibreOffice (`soffice`) + poppler (`pdftoppm`) | LibreOffice **24.2** recommended | visual layout gate; thresholds were calibrated on 24.2.7.2 |
| Node.js | ≥ 18 | frontend |
| [Ollama](https://ollama.com) | any | local model (default `gpt-oss:latest`) |

Without LibreOffice the app still works, but the visual check cannot run, so
every result needs your review (or is blocked if `RESUME_VISUAL_GATE_REQUIRED=true`).

## Run locally

```bash
cp env.example .env              # adjust OLLAMA_MODEL if needed
uv lock && uv sync --extra dev   # uv.lock must be generated once and committed
ollama pull gpt-oss              # or any model; set OLLAMA_MODEL
(cd frontend && npm install)
./dev.sh                         # API on http://127.0.0.1:8000, UI on http://localhost:5173
```

API only: `./start.sh` (or `uv run uvicorn main:app`). Interactive API docs:
`http://127.0.0.1:8000/docs`.

macOS: `brew install --cask libreoffice && brew install poppler`.
Debian/Ubuntu: `apt install libreoffice-writer-nogui poppler-utils fonts-crosextra-carlito fonts-crosextra-caladea`.

### Docker

```bash
docker compose up --build                        # API on :8000, model at $OLLAMA_BASE_URL
docker compose --profile ollama up --build       # also start a local Ollama container
```

The image is based on Ubuntu 24.04 (LibreOffice 24.2.x, the calibrated series),
runs as a non-root user and stores data in the `resume-data` volume.

## Test and evaluate

```bash
uv run pytest                         # unit, integration, security, evaluation suites
uv run pytest -m "not renderer"       # without LibreOffice
uv run ruff check . && uv run ruff format --check . && uv run mypy app
(cd frontend && npx tsc --noEmit)

uv run python -m app.evaluation.fidelity_eval --out evaluation/reports    # gate calibration (~9 min)
uv run python -m app.evaluation.proposal_eval --out evaluation/reports    # validator vs labelled dataset
uv run python -m app.evaluation.proposal_eval --live --out evaluation/reports   # against your configured model
```

Tests use a scripted model, so they are deterministic and need no Ollama.
Renderer tests are skipped automatically when LibreOffice is absent.

## Configuration

All settings are environment variables, validated at startup; an invalid value
stops the server with a message naming the variable. See `env.example` for the
full list. The most important ones:

| Variable | Default | Notes |
|---|---|---|
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | `http://localhost:11434`, `gpt-oss:latest` | Resume text is sent here |
| `RESUME_LLM_PROVIDER` | `ollama` | `disabled` turns analysis off |
| `RESUME_LLM_TEMPERATURE` | `0.1` | low variance for structured edits |
| `RESUME_DEPLOYMENT_MODE` | `local` | `shared` requires `RESUME_API_AUTH_TOKEN` and `RESUME_SIGNING_SECRET` |
| `RESUME_SESSION_TTL_HOURS` | `24` | uploads, outputs and renders are deleted after this |
| `RESUME_MAX_UPLOAD_BYTES` | 5 MB | also uncompressed-size, member-count and ratio limits |
| `RESUME_RENDERER_EXPECTED_VERSION` | unset (`24.2` in Docker) | other renderer versions downgrade results to "needs review" |
| `RESUME_VISUAL_GATE_REQUIRED` | `false` | `true` blocks delivery when layout cannot be checked |
| `RESUME_LOG_CONTENT` | `false` | never enable in production |

## Privacy

Resume and job text go only to the configured model endpoint. The server warns
at startup if that endpoint is not local. Logs never contain resume or job text
by default. Files are deleted after the session TTL, or immediately via "Delete
my files" (`DELETE /api/sessions/{id}`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| "The language model service could not be reached" | Start Ollama (`ollama serve`) and check `OLLAMA_BASE_URL`; use **Try analysis again** |
| "Model service rejected the request" | `OLLAMA_MODEL` is not pulled: `ollama pull <model>` |
| Every result says "Rendered layout compared: Not run" | Install LibreOffice and poppler, or set `RESUME_SOFFICE_PATH` / `RESUME_PDFTOPPM_PATH`; check `GET /ready` |
| "Renderer version … differs from pinned" | Install LibreOffice 24.2, or recalibrate (`fidelity_eval`) and update `RESUME_RENDERER_EXPECTED_VERSION` |
| Upload rejected: "links to external content" | The document references a remote template or linked image; embed it in Word and re-save |
| "No editable text was found" | All text is in text boxes or other read-only elements; see the limitations in the fidelity doc |
| Proposals list is empty | The model found nothing it could support with evidence; add confirmed facts under "Facts you can confirm" |
