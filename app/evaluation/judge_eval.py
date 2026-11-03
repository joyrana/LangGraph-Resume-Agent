"""LLM-as-a-judge evaluation of edit proposals.

Calibrate (``--calibrate``): the judge grades every labelled case in
``evaluation/datasets/proposals_v1.json`` that targets a real paragraph, and
its fabrication flag is compared with the human ``claim`` label. Run this
first: a judge that misses fabrications or flags grounded edits cannot be used
to grade the generator.

Live (default): the configured model proposes edits for every corpus fixture
against a sample job description, the deterministic validator filters them as
in production, and the judge grades each surviving proposal. Reports pass rate,
mean rubric scores and every failing proposal with the judge's rationale.

Both modes need a reachable model server (mlx-lm by default). Set
``RESUME_JUDGE_MODEL`` to grade with a different model than the generator.
``--min-score`` makes the command exit non-zero below a threshold (CI gate).

Usage::

    python -m app.evaluation.judge_eval --calibrate --min-score 0.85
    python -m app.evaluation.judge_eval --min-score 0.9
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from statistics import mean
from typing import Any

from app.core.errors import LLMError
from app.document.docx_parser import parse_docx
from app.evaluation.corpus import FIXTURES, fixture_by_id
from app.evaluation.llm_judge import JUDGE_PROMPT_VERSION, JudgeCase, JudgeResult, judge_edit
from app.evaluation.proposal_eval import DATASET, SAMPLE_JD
from app.llm.provider import LLMProvider
from app.resume.resume_analyzer import build_resume_context

CRITERIA = ("faithfulness", "meaning_preservation", "writing_quality", "job_relevance")


def calibration_cases(dataset_path: Path = DATASET) -> list[tuple[JudgeCase, bool]]:
    """(case, is_unsupported_claim) for labelled cases the judge can meaningfully grade.

    Cases that fail on targeting (unknown location, misquote, ambiguity) are
    skipped: those are mechanical checks, not judgement calls.
    """
    dataset = json.loads(dataset_path.read_text())
    cases = []
    for case in dataset["cases"]:
        if case["locate"] is None:
            continue
        index, _ = parse_docx(fixture_by_id(case["fixture"]).build())
        matches = [b for b in index.blocks if case["locate"] in b.text]
        if len(matches) != 1 or matches[0].text.count(case["original"]) != 1:
            continue
        paragraph = matches[0].text
        judge_case = JudgeCase(
            case_id=case["id"],
            job_description=dataset["default_jd"] if case.get("jd") is None else case["jd"],
            candidate_notes=case.get("notes", ""),
            resume_text=index.full_text(),
            paragraph_before=paragraph,
            paragraph_after=paragraph.replace(case["original"], case["proposed"], 1),
            edit_type=case["edit_type"],
        )
        cases.append((judge_case, bool(case["claim"])))
    return cases


async def run_calibration(provider: LLMProvider, dataset_path: Path = DATASET) -> dict:
    rows: list[dict[str, Any]] = []
    for case, is_claim in calibration_cases(dataset_path):
        try:
            result = await judge_edit(provider, case)
        except LLMError as exc:
            rows.append({"id": case.case_id, "label_claim": is_claim, "error": exc.code, "correct": False})
            continue
        rows.append({**result.as_row(), "label_claim": is_claim, "correct": result.flags_fabrication == is_claim})

    graded = [r for r in rows if "error" not in r]
    claims = [r for r in graded if r["label_claim"]]
    grounded = [r for r in graded if not r["label_claim"]]
    return {
        "mode": "calibration",
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "provider": provider.name,
        "judge_model": provider.model,
        "cases": len(rows),
        "errors": len(rows) - len(graded),
        # Primary score: agreement of the judge's fabrication flag with human labels.
        "score": round(sum(r["correct"] for r in rows) / max(1, len(rows)), 4),
        "fabrication_recall": round(sum(r["flags_fabrication"] for r in claims) / max(1, len(claims)), 4),
        "false_alarm_rate": round(sum(r["flags_fabrication"] for r in grounded) / max(1, len(grounded)), 4),
        "disagreements": [r for r in rows if not r["correct"]],
        "rows": rows,
    }


async def run_live(generator: LLMProvider, judge: LLMProvider, settings) -> dict:
    from app.resume.edit_proposer import propose_edits

    results: list[JudgeResult] = []
    errors = []
    for fx in FIXTURES:
        index, pkg = parse_docx(fx.build())
        try:
            run = await propose_edits(
                provider=generator,
                index=index,
                pkg=pkg,
                job_description=SAMPLE_JD,
                company_details="",
                candidate_notes="",
                char_budget=settings.llm_context_char_budget,
                max_proposals=settings.max_proposals,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
            )
        except LLMError as exc:
            errors.append({"fixture": fx.fixture_id, "stage": "generate", "error": exc.code})
            continue
        resume_text, _, _ = build_resume_context(index, settings.llm_context_char_budget)
        for n, proposal in enumerate(run.proposals):
            before = proposal.paragraph_text
            case = JudgeCase(
                case_id=f"{fx.fixture_id}#{n}",
                job_description=SAMPLE_JD,
                candidate_notes="",
                resume_text=resume_text,
                paragraph_before=before,
                paragraph_after=before[: proposal.start] + proposal.proposed_text + before[proposal.end :],
                edit_type=proposal.edit_type.value,
                reason=proposal.reason,
            )
            try:
                results.append(await judge_edit(judge, case))
            except LLMError as exc:
                errors.append({"fixture": fx.fixture_id, "stage": "judge", "case": case.case_id, "error": exc.code})

    rows = [r.as_row() for r in results]
    return {
        "mode": "live",
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "generator_model": generator.model,
        "judge_model": judge.model,
        "fixtures": len(FIXTURES),
        "proposals_judged": len(rows),
        "errors": errors,
        # Primary score: share of validator-approved proposals the judge also passes (None if nothing survived).
        "score": round(sum(r["passed"] for r in rows) / len(rows), 4) if rows else None,
        "mean_scores": {c: round(mean(r[c] for r in rows), 2) if rows else None for c in CRITERIA},
        "failures": [r for r in rows if not r["passed"]],
        "rows": rows,
    }


async def _main_async(args: argparse.Namespace) -> dict:
    from app.core.config import get_settings
    from app.evaluation.llm_judge import build_judge_provider
    from app.llm.provider import build_provider

    settings = get_settings()
    judge = build_judge_provider(settings)
    try:
        if args.calibrate:
            return await run_calibration(judge)
        generator = build_provider(settings)
        try:
            return await run_live(generator, judge, settings)
        finally:
            await generator.close()
    finally:
        await judge.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("evaluation/reports"))
    parser.add_argument("--calibrate", action="store_true", help="grade the labelled dataset and measure agreement with human labels")
    parser.add_argument("--min-score", type=float, default=None, help="exit 1 if the report score is below this value")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    report = asyncio.run(_main_async(args))
    name = "judge_calibration.json" if args.calibrate else "judge_eval_live.json"
    (args.out / name).write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k not in ("rows",)}, indent=2))
    if args.min_score is not None and (report["score"] is None or report["score"] < args.min_score):
        reason = "no proposals to judge" if report["score"] is None else f"score {report['score']} < --min-score {args.min_score}"
        print(f"FAIL: {reason}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
