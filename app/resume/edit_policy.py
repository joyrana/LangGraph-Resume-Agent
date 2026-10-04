"""Edit policy: limits and claim-strength vocabularies used by the evidence validator."""

from __future__ import annotations

import re

# Replacement must stay "small and reviewable".
MAX_ABSOLUTE_GROWTH_CHARS = 120
MAX_RELATIVE_GROWTH = 1.0  # proposed may be at most 2x the original quote (+ absolute allowance)
MIN_GROWTH_ALLOWANCE = 40

# Verbs that assert an outcome. Introducing one without evidence turns a duty into an achievement.
OUTCOME_VERBS = {
    "increas",
    "reduc",
    "improv",
    "boost",
    "grew",
    "grow",
    "sav",
    "generat",
    "doubl",
    "tripl",
    "accelerat",
    "cut",
    "eliminat",
    "maximiz",
    "minimiz",
    "exceed",
    "outperform",
    "deliver",
    "achiev",
    "drove",
    "driv",
}
# Verbs that assert leadership or ownership.
LEADERSHIP_VERBS = {
    "led",
    "lead",
    "manag",
    "spearhead",
    "own",
    "direct",
    "head",
    "supervis",
    "architect",
    "champion",
    "orchestrat",
    "oversaw",
    "oversee",
    "pioneer",
    "found",
    "establish",
    "mentor",
}

URL_OR_EMAIL = re.compile(r"(https?://|www\.|\b[\w.+-]+@[\w-]+\.[\w.]+\b|\b[\w-]+\.(com|org|net|io|dev|ai)\b)", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z]+")


def stems(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)}


def introduced_verbs(original: str, proposed: str, evidence: str, vocabulary: set[str]) -> list[str]:
    """Vocabulary stems that occur in ``proposed`` but not in ``original`` or ``evidence``."""
    before = stems(original) | stems(evidence)
    found: list[str] = []
    for word in sorted(stems(proposed)):
        stem = next((v for v in vocabulary if word.startswith(v)), None)
        if stem is None:
            continue
        if any(b.startswith(stem) for b in before):
            continue
        found.append(word)
    return found
