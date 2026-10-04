# Fidelity Evaluation

The fidelity evaluator decides whether an edited document may be delivered. It
compares the edited output against the original upload and the exact set of
accepted edits, never against model output.

## Gates

| Gate | What is checked | Failure means |
|---|---|---|
| **A. Integrity** | Output passes the same validator as uploads (ZIP, content types, XML safety, external relationships); reopens with our parser and python-docx; CRC check; no part from the original is missing; external hyperlink count unchanged | FAIL |
| **B. Structure** | Every ZIP member except parts containing edited paragraphs is byte-identical. Within edited parts, XML is identical after blanking `w:t` text of the edited paragraphs only. Independent re-parse compares section page size, orientation, margins, columns, feature counts (tables, nested tables, images, hyperlinks, fields, text boxes, tracked changes, headers, footers…) and per-paragraph style, heading level, list level, container, table cell position and run-format signatures | FAIL |
| **C. Content** | Each paragraph's text equals the original with exactly the accepted edits applied; no rejected edit appears; no duplicated paragraphs; numbers in accepted edits are traceable to the resume or user-provided facts | FAIL |
| **D. Visual** | Original and output are rendered with LibreOffice (isolated profile, fixed DPI). Two extra *marker* renders colour the edited paragraphs magenta, which locates expected-change regions exactly (colour does not alter layout; this is verified on every run). Checks: page count, blank pages, context words missing/extra, font/size/colour changes, shifts before the first edit, words moving to another page, line-wrap changes, text outside the page, image count/size, table borders and shapes, and pixel differences outside expected regions (per-channel colour, shift-compensated below an edit, 1 px tolerant to sub-pixel reflow). SSIM is recorded per page but is never a gate | WARNING → review; blank page or missing image → FAIL |
| **E. Decision** | Any FAIL → **FAIL** (download blocked, cannot be acknowledged). Visual gate not run → **REVIEW_REQUIRED** (or FAIL when `RESUME_VISUAL_GATE_REQUIRED=true`). Renderer version differs from `RESUME_RENDERER_EXPECTED_VERSION` → REVIEW_REQUIRED. Any warning → REVIEW_REQUIRED (download after explicit acknowledgement). Otherwise **PASS** | |

A changed page count is a warning, not a failure: an approved edit may
legitimately reflow text. Pure vertical reflow on the same page below an edit is
informational only.

## Calibration method

`python -m app.evaluation.fidelity_eval` produces `evaluation/reports/fidelity_eval.json`.

* **Corpus** (`app/evaluation/corpus.py`, version `2026.10.1`): 10 deterministic
  fixtures covering one-page and three-page resumes; mixed run formatting and
  words split across runs; bullets and nested numbering; tables, merged cells
  and nested tables; continuous section break into two columns plus a landscape
  section; headers, footers, PAGE/DATE fields and hyperlinks; block and inline
  images; uncommon fonts, exact line spacing, small caps and character spacing;
  tracked changes, inline content controls, bookmarks and VML text boxes.
* **Negatives:** 15 controlled edits (short and long replacements, ranges
  spanning runs, edits in tables, nested tables, columns, headers and next to
  hyperlinks), each with an expected decision. All satisfy the proposal size
  policy. One deliberately pushes content across a page break and must yield
  REVIEW_REQUIRED.
* **Positives:** 18 injected regressions applied after a correct edit: font
  size, unedited text, fabricated metric, bold removal, colour, paragraph
  spacing, margins, page break, removed image, table column width, duplicated
  paragraph, letter spacing, yellow highlight, cell shading, image content swap
  (the last three are pixel-only), truncated package, external template
  relationship, and an unrelated part modified. 139 regression cases in total.
* **Threshold sweep:** the visual gate alone (gates A–C bypassed) is evaluated
  for 27 combinations of position tolerance (0.25/0.75/1.5 pt), pixel
  threshold (32/48/96 per channel) and minimum region area (4/16/64 px).

Environment: LibreOffice 24.2.7.2, poppler `pdftoppm`, 100 DPI, Linux x86-64,
Carlito/Caladea/DejaVu/Liberation fonts.

## Results

End to end (all gates):

| Metric | Result |
|---|---|
| Regressions blocked (FAIL) | 139 / 139 |
| Controlled edits with the expected decision | 15 / 15 before the shift-compensation change; see note below |
| Proposal validator vs labelled dataset (`proposals/2026.10.1`, 33 cases) | 33 / 33; 16 / 16 unsupported or inflated claims blocked; 0 / 11 grounded edits rejected |

Visual gate alone, how calibration changed the design:

| Iteration | Change | Missed regressions | False alarms |
|---|---|---|---|
| 1 | Word geometry + grayscale pixels | 2 / 98 (colour changes below a reflow) | 0 / 14 |
| 2 | + word colour comparison | 0 / 98 | 0 / 14 |
| 3 | + 3 pixel-only regressions, per-channel colour diff | 1–2 / 110 depending on pixel threshold (32 catches light cell shading, 48 does not) | 0 / 14 |
| 4 | + shift-compensated diff below edits | 0 / 110 at every threshold | **1 / 14** (`tables/edit0`: sub-pixel reflow anti-aliasing) |
| 5 | + ±1 px tolerant shifted diff | spot-checked: highlight and shading below the reflow still caught | spot-checked: `tables/edit0` passes |

Iteration 5 is covered by `test_visual_gate_reflowed_table_clean_but_detects_changes_below_reflow`.
The committed `fidelity_eval.json` is from iteration 4; rerun the calibration to
refresh it. Defaults chosen from the sweep: position tolerance 0.75 pt, pixel
threshold 32, minimum region 16 px.

## Limitations

* **LibreOffice is a proxy for Word.** Word may wrap lines or break pages
  differently; the UI says so. Thresholds apply only to the calibrated
  renderer version.
* **Small, synthetic corpus.** Fixtures are generated by python-docx, not
  authored in Word. There are only 14–15 negative cases, so a 0% false-alarm
  rate is not an estimate of the real-world rate. Real Word templates
  (text-box-heavy designs, theme fonts, complex field nesting, right-to-left
  scripts) are not represented. Expand the corpus with real, consented resumes
  before relying on the numbers.
* **Content moved to another page is not pixel-compared.** It is still covered
  by word-level checks (missing words, font, colour, line wrap) and by gates B
  and C.
* **Self-authored labels.** The proposal dataset was written alongside the
  validator; agreement is a regression guard, not an accuracy measure. The
  validator is deliberately conservative: new word forms ("Dockerized") and
  abbreviations ("CKA") that do not appear in the resume are rejected.
* **Read-only content.** Text boxes, field results, tracked changes, inline or
  locked content controls, equations, footnotes, endnotes, comments, charts and
  SmartArt are preserved but cannot be edited. Edits cannot cross hyperlinks,
  images, bookmarks, fields, tabs or line breaks, and cannot insert line breaks.
* **Not run here:** `proposal_eval --live` against a real model; results on
  other LibreOffice versions.
