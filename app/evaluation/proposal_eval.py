"""Proposal-quality evaluation.

Offline (default): runs the labelled dataset in ``evaluation/datasets`` through
the deterministic validator and reports agreement with the labels, the
unsupported-claim block rate, and false rejections of grounded edits.

Live (``--live``): calls the configured model on every corpus fixture with a
sample job description and reports schema-validity rate, evidence-grounding
rate (proposals surviving validation / proposals returned), rejection reasons,
latency and token counts. Requires a reachable model (e.g. Ollama).

Usage::

    python -m app.evaluation.proposal_eval --out evaluation/reports
    python -m app.evaluation.proposal_eval --live --out evaluation/reports
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from app.document.docx_parser import parse_docx
from app.evaluation.corpus import FIXTURES, fixture_by_id
from app.resume.evidence_validator import EvidenceValidator
from app.resume.proposal_models import LLMProposalBatch

DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "proposals_v1.json"


def _location(index, locate: str | None) -> str:
    if locate is None:
        return "loc_0000000000000000"
    matches = [b for b in index.blocks if locate in b.text]
    if len(matches) != 1:
        raise ValueError(f"locator {locate!r} matched {len(matches)} blocks")
    return matches[0].location_id


def run_offline(dataset_path: Path = DATASET) -> dict:
    dataset = json.loads(dataset_path.read_text())
    rows = []
    for case in dataset["cases"]:
        data = fixture_by_id(case["fixture"]).build()
        index, pkg = parse_docx(data)
        loc = _location(index, case["locate"])
        evidence = case.get("evidence") or [[case["locate"], case["original"]]] if case["locate"] else [[None, case["original"]]]
        ev = [{"location_id": _location(index, n) if n else loc, "quote": q} for n, q in evidence]
        raw = {
            "location_id": loc,
            "original_text": case["original"],
            "proposed_text": case["proposed"],
            "edit_type": case["edit_type"],
            "reason": "eval",
            "supporting_evidence": ev,
            "confidence": 0.8,
        }
        validator = EvidenceValidator(
            index, pkg, dataset["default_jd"] if case.get("jd") is None else case["jd"], case.get("notes", ""), 25
        )
        outcome = validator.validate(LLMProposalBatch.model_validate({"proposals": [raw]}))
        if outcome.accepted:
            actual = "accept_confirm" if outcome.accepted[0].requires_user_confirmation else "accept"
        else:
            actual = outcome.rejected[0].code
        rows.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "actual": actual,
                "correct": actual == case["expected"],
                "claim": case["claim"],
            }
        )

    claims = [r for r in rows if r["claim"]]
    grounded = [r for r in rows if r["expected"] in ("accept", "accept_confirm")]
    return {
        "dataset_version": dataset["dataset_version"],
        "cases": len(rows),
        "label_agreement": round(sum(r["correct"] for r in rows) / len(rows), 4),
        "unsupported_or_inflated_claims": len(claims),
        # A claim case is "blocked" if it is rejected or forced through explicit user confirmation.
        "claim_block_rate": round(sum(r["actual"] != "accept" for r in claims) / max(1, len(claims)), 4),
        "grounded_cases": len(grounded),
        "grounded_false_rejection_rate": round(
            sum(r["actual"] not in ("accept", "accept_confirm") for r in grounded) / max(1, len(grounded)), 4
        ),
        "disagreements": [r for r in rows if not r["correct"]],
        "rows": rows,
    }


SAMPLE_JD = (
    "We are hiring a backend engineer to build Python services with FastAPI and PostgreSQL. You will review code, mentor "
    "junior engineers and improve reliability. Experience with Docker is required; Kubernetes and Terraform are a plus."
)


async def run_live() -> dict:
    from app.core.config import get_settings
    from app.core.errors import LLMError
    from app.llm.provider import build_provider
    from app.resume.edit_proposer import propose_edits

    settings = get_settings()
    provider = build_provider(settings)
    results = []
    try:
        for fx in FIXTURES:
            data = fx.build()
            index, pkg = parse_docx(data)
            started = time.monotonic()
            try:
                run = await propose_edits(
                    provider=provider,
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
                results.append(
                    {
                        "fixture": fx.fixture_id,
                        "schema_valid": True,
                        "attempts": run.model_metadata["attempts"],
                        "returned": run.model_metadata["raw_proposals"],
                        "valid": len(run.proposals),
                        "high_risk": sum(p.requires_user_confirmation for p in run.proposals),
                        "rejections": dict(Counter(r.code for r in run.rejected)),
                        "latency_ms": int((time.monotonic() - started) * 1000),
                        "prompt_tokens": run.model_metadata.get("prompt_tokens"),
                        "completion_tokens": run.model_metadata.get("completion_tokens"),
                    }
                )
            except LLMError as exc:
                results.append(
                    {
                        "fixture": fx.fixture_id,
                        "schema_valid": False,
                        "error": exc.code,
                        "latency_ms": int((time.monotonic() - started) * 1000),
                    }
                )
    finally:
        await provider.close()
    returned = sum(r.get("returned", 0) for r in results)
    valid = sum(r.get("valid", 0) for r in results)
    return {
        "provider": provider.name,
        "model": provider.model,
        "runs": len(results),
        "schema_validity_rate": round(sum(r["schema_valid"] for r in results) / max(1, len(results)), 4),
        "evidence_grounding_rate": round(valid / max(1, returned), 4),
        "proposals_returned": returned,
        "proposals_valid": valid,
        "rejection_reasons": dict(sum((Counter(r.get("rejections", {})) for r in results), Counter())),
        "median_latency_ms": sorted(r["latency_ms"] for r in results)[len(results) // 2] if results else None,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("evaluation/reports"))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.live:
        report = asyncio.run(run_live())
        (args.out / "proposal_eval_live.json").write_text(json.dumps(report, indent=2))
    else:
        report = run_offline()
        (args.out / "proposal_eval_offline.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k not in ("rows", "results")}, indent=2))


if __name__ == "__main__":
    main()
