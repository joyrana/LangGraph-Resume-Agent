"""Deterministic job-description term extraction and coverage estimate.

The estimate counts how many salient JD terms appear in the resume text. It
is labelled as an estimate everywhere it is shown and is never produced by
the model, so a model failure can never silently fall back to a made-up
score (the old code defaulted to 65).
"""

from __future__ import annotations

import re
from collections import Counter

from app.resume.claims import named_terms, normalize_term
from app.resume.proposal_models import AlignmentEstimate

_STOP = {
    "about",
    "above",
    "across",
    "after",
    "also",
    "among",
    "and",
    "any",
    "are",
    "able",
    "ability",
    "based",
    "been",
    "being",
    "both",
    "but",
    "can",
    "candidate",
    "company",
    "work",
    "working",
    "will",
    "with",
    "within",
    "without",
    "you",
    "your",
    "our",
    "the",
    "their",
    "this",
    "that",
    "these",
    "those",
    "team",
    "teams",
    "role",
    "roles",
    "join",
    "must",
    "should",
    "including",
    "strong",
    "experience",
    "years",
    "year",
    "plus",
    "preferred",
    "required",
    "requirements",
    "responsibilities",
    "skills",
    "knowledge",
    "understanding",
    "excellent",
    "good",
    "great",
    "using",
    "use",
    "new",
    "who",
    "what",
    "where",
    "when",
    "how",
    "from",
    "into",
    "have",
    "has",
    "had",
    "more",
    "other",
    "such",
    "well",
    "per",
    "etc",
    "we",
    "us",
    "for",
    "job",
    "position",
    "opportunity",
    "equal",
    "employer",
    "benefits",
    "salary",
    "location",
    "remote",
    "hybrid",
    "apply",
}

# Common words that start JD sentences; capitalised occurrences are not product/skill names.
_COMMON_INITIAL = {
    "senior",
    "junior",
    "lead",
    "principal",
    "staff",
    "the",
    "this",
    "that",
    "these",
    "we",
    "you",
    "our",
    "your",
    "they",
    "it",
    "in",
    "at",
    "as",
    "on",
    "of",
    "to",
    "for",
    "with",
    "by",
    "an",
    "a",
    "if",
    "all",
    "each",
    "every",
    "some",
    "strong",
    "solid",
    "proven",
    "deep",
    "hands",
    "nice",
    "bonus",
    "must",
    "should",
    "ideally",
    "preferably",
    "minimum",
    "bachelor",
    "bachelors",
    "master",
    "masters",
    "degree",
    "responsibilities",
    "requirements",
    "qualifications",
    "about",
    "join",
    "work",
    "build",
    "design",
    "develop",
    "help",
    "own",
    "drive",
    "partner",
    "collaborate",
    "ability",
    "knowledge",
    "familiarity",
    "plus",
    "preferred",
    "required",
    "what",
    "who",
    "why",
    "how",
    "when",
    "where",
    "key",
    "able",
    "ensure",
    "maintain",
    "write",
    "create",
    "manage",
    "support",
    "communicate",
    "excellent",
    "great",
    "good",
    "experience",
    "experienced",
    "background",
    "understanding",
    "engineer",
    "engineers",
    "developer",
    "role",
    "team",
    "company",
    "candidate",
    "candidates",
    "benefits",
    "salary",
    "location",
    "remote",
    "hybrid",
    "full",
    "part",
    "time",
    "please",
    "apply",
    "equal",
    "opportunity",
    "employer",
    "is",
    "are",
    "be",
    "and",
    "or",
    "but",
    "so",
    "also",
    "using",
}

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-/]*[A-Za-z0-9+#]|[A-Za-z]{2,}")


def salient_terms(job_description: str, limit: int = 40) -> list[str]:
    """Named terms first (technologies, certifications), then frequent content words."""
    terms: list[str] = []
    seen: set[str] = set()
    for term in sorted(named_terms(job_description), key=lambda t: (-job_description.count(t), t)):
        key = normalize_term(term)
        if len(key) >= 2 and key not in seen and term.lower() not in _STOP:
            seen.add(key)
            terms.append(term)
    # Capitalised words at sentence starts are skipped by named_terms(); include them unless common.
    for raw in re.findall(r"(?:^|[.!?:;\n•\-]\s*)([A-Z][A-Za-z0-9+#.]*[A-Za-z0-9+#])", job_description):
        key = normalize_term(raw)
        if len(key) >= 2 and key not in seen and raw.lower() not in _STOP and raw.lower() not in _COMMON_INITIAL:
            seen.add(key)
            terms.append(raw)
    counts = Counter(w.lower() for w in _WORD.findall(job_description) if len(w) > 3 and w.lower() not in _STOP)
    for word, n in counts.most_common():
        if n < 2:
            break
        key = normalize_term(word)
        if key not in seen:
            seen.add(key)
            terms.append(word)
    return terms[:limit]


def _contains(haystack_norm_tokens: set[str], haystack_lower: str, term: str) -> bool:
    key = normalize_term(term)
    if not key:
        return False
    if key in haystack_norm_tokens:
        return True
    if " " in term or "-" in term:
        return term.lower() in haystack_lower
    return False


def coverage_estimate(job_description: str, resume_text: str) -> AlignmentEstimate:
    terms = salient_terms(job_description)
    if not terms:
        return AlignmentEstimate(coverage_percent=None, terms_considered=0)
    tokens = {normalize_term(t) for t in re.findall(r"\S+", resume_text)} | {normalize_term(t) for t in _WORD.findall(resume_text)}
    lower = resume_text.lower()
    matched = [t for t in terms if _contains(tokens, lower, t)]
    missing = [t for t in terms if t not in matched]
    return AlignmentEstimate(
        coverage_percent=round(100 * len(matched) / len(terms)),
        matched_terms=matched,
        missing_terms=missing,
        terms_considered=len(terms),
    )
