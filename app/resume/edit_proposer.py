"""Request structured edit proposals from the model and validate them."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.errors import LLMOutputInvalid
from app.document.document_models import DocumentIndex
from app.document.docx_parser import DocxPackage
from app.llm.provider import LLMProvider, LLMRequest, LLMResponse
from app.resume.evidence_validator import EvidenceValidator
from app.resume.prompts import PROMPT_VERSION, render_prompts
from app.resume.proposal_models import EditProposal, LLMProposal, LLMProposalBatch, RejectedProposal, llm_output_schema
from app.resume.resume_analyzer import build_resume_context

logger = logging.getLogger(__name__)

MAX_SCHEMA_ATTEMPTS = 2  # one initial call + one re-ask on malformed output
MAX_PROBLEMS_IN_REASK = 10

# Called as on_progress(step_id, human-readable detail); step ids are listed in app/workflow/progress.py.
ProgressCallback = Callable[[str, str], None]


def _no_progress(step: str, detail: str) -> None:
    return None


def validation_problems(exc: ValidationError, prefix: str = "") -> list[str]:
    """Readable ``path: message`` lines, quoted back to the model when re-asking."""
    return [f"{prefix}{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()]


def _parse_batch(response: LLMResponse) -> tuple[LLMProposalBatch, list[RejectedProposal], list[str]]:
    """Validate the envelope, then each proposal on its own.

    Servers without constrained decoding (e.g. mlx-lm) let small models drop a
    field from one proposal; that proposal is rejected instead of the batch.
    Raises ``LLMOutputInvalid`` / ``ValidationError`` when the reply is unusable.
    """
    data = response.parse_json()
    items = data.get("proposals", []) if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise LLMOutputInvalid("The reply was not a JSON object with a `proposals` list.")
    batch = LLMProposalBatch.model_validate({**data, "proposals": []})
    valid: list[LLMProposal] = []
    rejected: list[RejectedProposal] = []
    problems: list[str] = []
    for i, item in enumerate(items[:60]):
        try:
            valid.append(LLMProposal.model_validate(item))
        except ValidationError as exc:
            problems += validation_problems(exc, prefix=f"proposals.{i}.")
            src = item if isinstance(item, dict) else {}
            fields = sorted({str(err["loc"][0]) for err in exc.errors() if err["loc"]})
            rejected.append(
                RejectedProposal(
                    location_id=str(src.get("location_id", "")),
                    original_text=str(src.get("original_text", "")),
                    proposed_text=str(src.get("proposed_text", "")),
                    code="schema_invalid",
                    message=f"Model output had missing or invalid fields: {', '.join(fields) or 'proposal'}.",
                )
            )
    return batch.model_copy(update={"proposals": valid}), rejected, problems


@dataclass
class ProposalRun:
    proposals: list[EditProposal]
    rejected: list[RejectedProposal]
    alignment_summary: str
    gaps_without_evidence: list[str]
    context_truncated: bool
    model_metadata: dict = field(default_factory=dict)


async def propose_edits(
    *,
    provider: LLMProvider,
    index: DocumentIndex,
    pkg: DocxPackage,
    job_description: str,
    company_details: str,
    candidate_notes: str,
    char_budget: int,
    max_proposals: int,
    temperature: float,
    max_tokens: int,
    on_progress: ProgressCallback = _no_progress,
) -> ProposalRun:
    context, included, truncated = build_resume_context(index, char_budget)
    on_progress(
        "read_resume",
        f"Read {len(included)} paragraph{'s' if len(included) != 1 else ''}"
        + (" (long resume: only the first part fits in the AI's context)" if truncated else ""),
    )
    system, user = render_prompts(
        job_description=job_description,
        company_details=company_details,
        candidate_notes=candidate_notes,
        resume_context=context,
        truncated=truncated,
        max_proposals=max_proposals,
    )
    schema = llm_output_schema()
    best: tuple[LLMProposalBatch, list[RejectedProposal]] | None = None
    responses: list[LLMResponse] = []
    problems: list[str] = []
    for attempt in range(1, MAX_SCHEMA_ATTEMPTS + 1):
        prompt = user
        if attempt > 1:
            listed = "\n".join(f"- {p}" for p in problems[:MAX_PROBLEMS_IN_REASK])
            prompt += (
                "\n\nYour previous reply was not valid JSON for the schema. Fix these problems and reply again with JSON only "
                f"(every proposal needs every required field):\n{listed}"
            )
        on_progress(
            "ask_model",
            f"Waiting for {provider.model.rsplit('/', 1)[-1]} to reply"
            if attempt == 1
            else f"The reply had {len(problems)} problem(s); asking the model to fix them",
        )
        response = await provider.complete(
            LLMRequest(
                system=system, user=prompt, json_schema=schema, temperature=temperature, max_tokens=max_tokens, purpose="propose_edits"
            )
        )
        responses.append(response)
        try:
            candidate, schema_rejected, problems = _parse_batch(response)
        except LLMOutputInvalid:
            problems = ["the reply was not a single valid JSON object"]
        except ValidationError as exc:
            problems = validation_problems(exc)
        else:
            # Keep the attempt with the most usable proposals (a re-ask can come back worse).
            if best is None or (len(candidate.proposals), -len(schema_rejected)) > (len(best[0].proposals), -len(best[1])):
                best = (candidate, schema_rejected)
            if not problems:
                break
        logger.warning("llm_output_invalid", extra={"attempt": attempt, "problems": len(problems)})
    if best is None or (best[1] and not best[0].proposals):
        raise LLMOutputInvalid(f"The language model returned invalid output twice ({len(problems)} schema error(s)).")
    batch, schema_rejected = best

    # Proposals for paragraphs that were not shown to the model are rejected as unknown.
    allowed = set(included)
    count = len(batch.proposals)
    on_progress(
        "check_evidence",
        f"Checking {count} suggestion{'s' if count != 1 else ''} against your resume" if count else "No suggestions to check",
    )
    validator = EvidenceValidator(index, pkg, job_description, candidate_notes, max_proposals)
    outcome = validator.validate(batch)
    proposals = [p for p in outcome.accepted if p.location_id in allowed]
    rejected = (
        schema_rejected
        + outcome.rejected
        + [
            RejectedProposal(
                location_id=p.location_id,
                original_text=p.original_text,
                proposed_text=p.proposed_text,
                code="not_in_context",
                message="Location was not part of the model context.",
            )
            for p in outcome.accepted
            if p.location_id not in allowed
        ]
    )
    metadata = {
        "provider": responses[-1].provider,
        "model": responses[-1].model,
        "prompt_version": PROMPT_VERSION,
        "temperature": temperature,
        "attempts": len(responses),
        "transport_attempts": sum(r.attempts for r in responses),
        "latency_ms": sum(r.latency_ms for r in responses),
        "prompt_tokens": sum(r.prompt_tokens or 0 for r in responses) or None,
        "completion_tokens": sum(r.completion_tokens or 0 for r in responses) or None,
        "raw_proposals": len(batch.proposals) + len(schema_rejected),
        "schema_rejected": len(schema_rejected),
        "valid_proposals": len(proposals),
    }
    return ProposalRun(
        proposals=proposals,
        rejected=rejected,
        alignment_summary=batch.alignment_summary,
        gaps_without_evidence=batch.gaps_without_evidence,
        context_truncated=truncated,
        model_metadata=metadata,
    )
