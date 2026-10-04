"""Deterministic extraction of factual claim tokens (numbers, named terms).

Used to check that edits introduce no numbers, metrics, dates, technologies,
titles or certifications that cannot be traced to the resume itself (or to
facts the user explicitly provided).
"""

from __future__ import annotations

import re

_NUMBER_RE = re.compile(
    r"(?<![\w.])([$€£₹]?)\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(%|percent\b|[kKmMbB]\b|x\b|\+)?",
)
_NUMBER_WORDS = {
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
    "hundred",
    "hundreds",
    "thousand",
    "thousands",
    "million",
    "millions",
    "billion",
    "billions",
    "dozen",
    "dozens",
    "double",
    "doubled",
    "triple",
    "tripled",
    "half",
    "halved",
    "first",
    "second",
    "third",
    "single",
}
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./&'-]*[A-Za-z0-9+#]|[A-Za-z]")

# Words that commonly start sentences or are generic and therefore not "named terms"
# even when capitalised.
_GENERIC = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "i",
    "we",
    "our",
    "my",
    "via",
    "across",
    "over",
    "under",
    "using",
}


def extract_numeric_claims(text: str) -> set[str]:
    claims: set[str] = set()
    for currency, number, unit in _NUMBER_RE.findall(text):
        value = number.replace(",", "")
        if "." in value:
            value = value.rstrip("0").rstrip(".") or "0"
        unit = (unit or "").lower()
        if unit == "percent":
            unit = "%"
        claims.add(f"{currency}{value}{unit}")
    for word in re.findall(r"[A-Za-z]+", text):
        if word.lower() in _NUMBER_WORDS:
            claims.add(word.lower())
    return claims


def tokenize_words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def named_terms(text: str) -> set[str]:
    """Terms that look like names, technologies, titles or certifications.

    A token qualifies if it contains an uppercase letter after its first
    character (``FastAPI``, ``PostgreSQL``), is all-caps with 2+ letters (``AWS``),
    contains digits or symbols typical of tech names (``C++``, ``Node.js``,
    ``S3``), or is capitalised and not at the start of a sentence.
    """
    terms: set[str] = set()
    sentence_start = True
    for match in re.finditer(r"\S+", text):
        raw = match.group(0)
        token = raw.strip("()[]{}\"'`,;:!?")
        trailing_period = token.endswith(".") and not re.search(r"\.[A-Za-z]", token[:-1] or "")
        token = token.rstrip(".") if trailing_period else token
        if token and re.search(r"[A-Za-z]", token) and token.lower() not in _GENERIC:
            interior_upper = any(ch.isupper() for ch in token[1:])
            has_digit = any(ch.isdigit() for ch in token)
            has_symbol = bool(re.search(r"[+#]|\.[A-Za-z]", token))
            capitalised = token[0].isupper()
            if interior_upper or has_digit or has_symbol or (capitalised and not sentence_start):
                terms.add(token)
        sentence_start = raw.endswith((".", "!", "?", ":", ";")) or raw in {"—", "-", "–", "|", "•"}
    return terms


def normalize_term(term: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", term.lower())
