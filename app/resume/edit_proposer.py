"""Request structured edit proposals from the model and validate them."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.errors import LLMOutputInvalid
from app.document.document_models import DocumentIndex
from app.document.docx_parser import DocxPackage
from app.llm.provider import LLMProvider, LLMRequest, LLMResponse
from app.resume.evidence_validator import EvidenceValidator
from app.resume.prompts import PROMPT_VERSION, render_prompts
from app.resume.proposal_models import EditProposal, LLMProposalBatch, RejectedProposal, llm_output_schema
from app.resume.resume_analyzer import build_resume_context

logger = logging.getLogger(__name__)

MAX_SCHEMA_ATTEMPTS = 2  # one initial call + one re-ask on malformed output


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
) -> ProposalRun:
    context, included, truncated = build_resume_context(index, char_budget)
    system, user = render_prompts(
        job_description=job_description,
        company_details=company_details,
        candidate_notes=candidate_notes,
        resume_context=context,
        truncated=truncated,
        max_proposals=max_proposals,
    )
    schema = llm_output_schema()
    batch: LLMProposalBatch | None = None
    responses: list[LLMResponse] = []
    last_problem = ""
    for attempt in range(1, MAX_SCHEMA_ATTEMPTS + 1):
        prompt = (
            user
            if attempt == 1
            else (user + f"\n\nYour previous reply was not valid JSON for the schema ({last_problem}). Reply again with JSON only.")
        )
        response = await provider.complete(
            LLMRequest(
                system=system, user=prompt, json_schema=schema, temperature=temperature, max_tokens=max_tokens, purpose="propose_edits"
            )
        )
        responses.append(response)
        try:
            batch = LLMProposalBatch.model_validate(response.parse_json())
            break
        except LLMOutputInvalid:
            last_problem = "malformed JSON"
        except ValidationError as exc:
            last_problem = f"{exc.error_count()} schema error(s)"
        logger.warning("llm_output_invalid", extra={"attempt": attempt, "problem": last_problem})
    if batch is None:
        raise LLMOutputInvalid(f"The language model returned invalid output twice ({last_problem}).")

    # Proposals for paragraphs that were not shown to the model are rejected as unknown.
    allowed = set(included)
    validator = EvidenceValidator(index, pkg, job_description, candidate_notes, max_proposals)
    outcome = validator.validate(batch)
    proposals = [p for p in outcome.accepted if p.location_id in allowed]
    rejected = outcome.rejected + [
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
        "raw_proposals": len(batch.proposals),
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
