from __future__ import annotations

import pytest

from app.document.docx_parser import parse_docx
from app.resume.claims import extract_numeric_claims, named_terms
from app.resume.evidence_validator import EvidenceValidator
from app.resume.proposal_models import LLMProposalBatch, RiskLevel
from tests.conftest import fixture_bytes
from tests.support.llm_fakes import block, proposal

JD = "We need a Python engineer with Kubernetes, Terraform and GraphQL experience. Kubernetes is essential. Python services at scale."


@pytest.fixture
def ctx():
    data = fixture_bytes("simple_one_page")
    index, pkg = parse_docx(data)
    return index, pkg


def _run(ctx, *items, notes: str = "", jd: str = JD, max_proposals: int = 25):
    index, pkg = ctx
    validator = EvidenceValidator(index, pkg, jd, notes, max_proposals)
    return validator.validate(LLMProposalBatch.model_validate({"proposals": list(items)}))


def _codes(outcome):
    return [r.code for r in outcome.rejected]


def test_valid_clarity_edit_accepted(ctx):
    index, _ = ctx
    out = _run(ctx, proposal(index, "Worked on the migration", "Worked on the migration of", "Migrated"))
    assert not out.rejected
    p = out.accepted[0]
    assert p.risk_level == RiskLevel.LOW and not p.requires_user_confirmation
    assert p.source_document_version == index.document_version
    assert p.paragraph_text[p.start : p.end] == "Worked on the migration of"
    assert p.edit_id.startswith("edt_")


def test_unknown_location_rejected(ctx):
    index, _ = ctx
    item = proposal(index, "Worked on the migration", "Worked on", "Drove")
    item["location_id"] = "loc_0000000000000000"
    assert _codes(_run(ctx, item)) == ["unknown_location"]


def test_misquoted_original_rejected(ctx):
    index, _ = ctx
    item = proposal(index, "Worked on the migration", "Worked on", "Drove")
    item["original_text"] = "worked ON"
    assert _codes(_run(ctx, item)) == ["target_not_found"]


def test_ambiguous_original_rejected():
    index, pkg = parse_docx(fixture_bytes("multipage"))
    item = proposal(index, "Delivered feature set 1 for the Northwind", "design", "product design", edit_type="clarity")
    # "and" occurs twice within this paragraph
    item["original_text"] = "and"
    out = EvidenceValidator(index, pkg, "", "", 25).validate(LLMProposalBatch.model_validate({"proposals": [item]}))
    assert [r.code for r in out.rejected] == ["ambiguous_target"]


def test_read_only_target_rejected():
    index, pkg = parse_docx(fixture_bytes("complex_elements"))
    item = proposal(index, "Text box", "open to relocation", "open to relocating")
    out = EvidenceValidator(index, pkg, "", "", 25).validate(LLMProposalBatch.model_validate({"proposals": [item]}))
    assert [r.code for r in out.rejected] == ["read_only_location"]


def test_range_crossing_read_only_content_rejected():
    index, pkg = parse_docx(fixture_bytes("complex_elements"))
    item = proposal(index, "Availability:", "Availability: Immediately", "Available immediately")
    out = EvidenceValidator(index, pkg, "", "", 25).validate(LLMProposalBatch.model_validate({"proposals": [item]}))
    assert [r.code for r in out.rejected] == ["read_only_text"]


def test_fabricated_evidence_rejected(ctx):
    index, _ = ctx
    item = proposal(index, "Worked on the migration", "Worked on", "Drove")
    item["supporting_evidence"][0]["quote"] = "Led the migration of 40 services"
    assert _codes(_run(ctx, item)) == ["evidence_not_found"]


def test_evidence_must_quote_the_cited_location(ctx):
    index, _ = ctx
    item = proposal(
        index, "Worked on the migration", "Worked on", "Drove", evidence=[("Python, FastAPI, PostgreSQL", "Worked on the migration")]
    )
    assert _codes(_run(ctx, item)) == ["evidence_not_found"]


def test_unsupported_number_rejected(ctx):
    index, _ = ctx
    item = proposal(
        index,
        "Worked on the migration",
        "Worked on the migration of the billing system",
        "Migrated the billing system, cutting costs by 30%,",
    )
    assert _codes(_run(ctx, item)) == ["unsupported_number"]


def test_changing_a_stated_number_rejected_even_if_value_appears_elsewhere(ctx):
    index, _ = ctx
    item = proposal(index, "Responsible for code reviews", "mentoring two junior engineers", "mentoring five junior engineers")
    assert _codes(_run(ctx, item)) == ["changed_number"]  # "five" occurs in "five years of experience"


def test_number_allowed_when_user_provided(ctx):
    index, _ = ctx
    item = proposal(
        index, "Worked on the migration", "Worked on the migration of the billing system", "Migrated the billing system (30% cheaper)"
    )
    out = _run(ctx, item, notes="The billing migration made hosting 30% cheaper.")
    assert not out.rejected, out.rejected


def test_number_from_elsewhere_in_resume_warns(ctx):
    index, _ = ctx
    item = proposal(index, "Worked on the migration", "Worked on the migration of", "Worked on the 2M-request migration of")
    out = _run(ctx, item)
    # "2M" is in another bullet, not in the cited evidence
    assert not out.rejected
    assert out.accepted[0].risk_level == RiskLevel.MEDIUM and out.accepted[0].validation_warnings


def test_job_description_keyword_without_evidence_rejected(ctx):
    index, _ = ctx
    item = proposal(
        index,
        "Built REST APIs",
        "Built REST APIs in Python and FastAPI",
        "Built REST APIs in Python, FastAPI and Kubernetes",
        edit_type="keyword_alignment",
    )
    assert _codes(_run(ctx, item)) == ["jd_keyword_without_evidence"]


def test_lowercase_jd_keyword_without_evidence_rejected(ctx):
    index, _ = ctx
    item = proposal(index, "Built REST APIs", "Built REST APIs", "Built REST and graphql APIs", edit_type="keyword_alignment")
    assert _codes(_run(ctx, item)) == ["jd_keyword_without_evidence"]


def test_invented_technology_rejected(ctx):
    index, _ = ctx
    item = proposal(index, "Built REST APIs", "Built REST APIs in Python", "Built REST APIs in Python and Rust")
    assert _codes(_run(ctx, item)) == ["unsupported_term"]


def test_compound_with_unsupported_part_rejected(ctx):
    index, _ = ctx
    item = proposal(index, "Built REST APIs", "Built REST APIs", "Built Kubernetes-based REST APIs", edit_type="keyword_alignment")
    assert _codes(_run(ctx, item)) == ["jd_keyword_without_evidence"]


def test_lowercase_compound_with_jd_part_rejected(ctx):
    index, _ = ctx
    item = proposal(index, "Built REST APIs", "Built REST APIs", "Built graphql-based REST APIs", edit_type="keyword_alignment")
    assert _codes(_run(ctx, item)) == ["jd_keyword_without_evidence"]


def test_keyword_supported_by_cited_evidence_accepted(ctx):
    index, _ = ctx
    item = proposal(
        index,
        "Built REST APIs",
        "Built REST APIs in Python",
        "Built containerized REST APIs in Python with Docker",
        edit_type="keyword_alignment",
        evidence=[("Built REST APIs", "Built REST APIs in Python"), ("Python, FastAPI, PostgreSQL", "Docker")],
    )
    out = _run(ctx, item, jd="Python, Docker, containerized services")
    assert not out.rejected, out.rejected
    assert out.accepted[0].risk_level == RiskLevel.MEDIUM


def test_leadership_inflation_requires_confirmation(ctx):
    index, _ = ctx
    item = proposal(index, "Responsible for code reviews", "Responsible for code reviews", "Led code reviews")
    out = _run(ctx, item)
    assert not out.rejected
    p = out.accepted[0]
    assert p.risk_level == RiskLevel.HIGH and p.requires_user_confirmation
    assert any("leadership" in w for w in p.validation_warnings)


def test_outcome_inflation_requires_confirmation(ctx):
    index, _ = ctx
    item = proposal(
        index, "Worked on the migration", "Worked on the migration of the billing system", "Improved the billing system by migrating it"
    )
    out = _run(ctx, item)
    assert out.accepted[0].requires_user_confirmation
    assert any("outcome" in w for w in out.accepted[0].validation_warnings)


@pytest.mark.parametrize(
    ("proposed", "code"),
    [
        ("Migrated\nthe", "invalid_characters"),
        ("Migrated\tthe", "invalid_characters"),
        ("   ", "empty_replacement"),
        ("Worked on the migration of", "no_change"),
        ("Worked on the migration of " + "very " * 40, "too_long"),
        ("See https://evil.example for details on", "unsupported_contact_or_link"),
        ("Email me at x@evil.example about", "unsupported_contact_or_link"),
    ],
)
def test_replacement_text_rules(ctx, proposed, code):
    index, _ = ctx
    assert _codes(_run(ctx, proposal(index, "Worked on the migration", "Worked on the migration of", proposed))) == [code]


def test_duplicates_and_overlaps_rejected(ctx):
    index, _ = ctx
    a = proposal(index, "Worked on the migration", "Worked on the migration of", "Migrated", confidence=0.9)
    dup = dict(a, confidence=0.5)
    overlap = proposal(index, "Worked on the migration", "migration of the billing", "move of the billing", confidence=0.4)
    out = _run(ctx, a, dup, overlap)
    assert len(out.accepted) == 1
    assert sorted(_codes(out)) == ["duplicate_proposal", "overlapping_proposal"]


def test_max_proposals_enforced(ctx):
    index, _ = ctx
    a = proposal(index, "Worked on the migration", "Worked on the migration of", "Migrated")
    b = proposal(index, "Responsible for code reviews", "Responsible for code reviews and mentoring", "Handled code reviews and mentoring")
    out = _run(ctx, a, b, max_proposals=1)
    assert len(out.accepted) == 1 and _codes(out) == ["over_limit"]


def test_edit_ids_are_stable(ctx):
    index, _ = ctx
    item = proposal(index, "Worked on the migration", "Worked on the migration of", "Migrated")
    assert _run(ctx, item).accepted[0].edit_id == _run(ctx, item).accepted[0].edit_id


def test_claim_extraction():
    assert extract_numeric_claims("Cut costs by 30% and saved $1,200 for 2 teams; 2M requests") >= {"30%", "$1200", "2", "2m"}
    assert "three" in extract_numeric_claims("three junior engineers")
    assert {"FastAPI", "PostgreSQL", "AWS", "C++", "Node.js"} <= named_terms("Used FastAPI, PostgreSQL, AWS, C++ and Node.js daily.")
    assert "Built" not in named_terms("Built things. Then more.")


def test_block_helper_sanity(ctx):
    index, _ = ctx
    assert block(index, "Built REST APIs").editable
