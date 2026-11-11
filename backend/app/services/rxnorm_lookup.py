"""Static RxNorm terminology lookup for the ClinCoherence T2DM prototype vocabulary.

Normalizes free-text medication names from confirmed patient MedicationFact records
to ingredient-level RxNorm concepts (RXCUIs). This is a read-only terminology lookup;
it never modifies patient records, makes clinical judgments, or performs dose calculations.

Scope: metformin, empagliflozin, semaglutide — the three medications approved for the
Phase 3 prototype. Extending the vocabulary requires adding to _ENTRIES and a code review.

Matching uses word-boundary-aware regex so that partial token overlap (e.g. a future
ingredient whose name contains "metformin" as a strict substring but is a distinct
molecule) cannot silently resolve to the wrong entry.  The word-boundary rules are:
  - Ingredient names that end with a letter/digit use \\b (standard word boundary).
  - Ingredient names that end with a punctuation character (none currently) would
    need a custom boundary; the validator below rejects such entries at startup.

RxNorm data is produced by the U.S. National Library of Medicine.
Source: https://www.nlm.nih.gov/research/umls/rxnorm/
RXCUIs validated against the RxNorm API (https://rxnav.nlm.nih.gov/), accessed 2024-11.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal


@dataclass(frozen=True)
class RxNormResult:
    """Immutable result of a medication name normalization lookup.

    status values:
      RESOLVED    — exactly one ingredient match found in the prototype vocabulary.
      AMBIGUOUS   — the name matched more than one ingredient entry simultaneously.
                    Callers must treat this as equivalent to NOT_FOUND for eligibility
                    purposes — no ingredient is assumed.
      NOT_FOUND   — no match in the prototype vocabulary; absence is not an error.

    A NOT_FOUND or AMBIGUOUS result means the eligibility service will treat the
    medication as outside the prototype scope, not that the medication is incorrect
    or harmful.  This result is never written back to any patient fact record.
    """

    rxcui: str
    ingredient_name: str
    status: Literal["RESOLVED", "AMBIGUOUS", "NOT_FOUND"]
    source_version: str  # RxNorm release date, e.g. "2024-11-04"


# ---------------------------------------------------------------------------
# Prototype vocabulary
# (ingredient_name, rxcui, compiled_word_boundary_pattern)
# ---------------------------------------------------------------------------
# Patterns are compiled once at import time.  Each uses a leading word boundary
# so that "metformin" does not match "dimetformin" (hypothetical).  The trailing
# boundary handles "metformin hydrochloride", "metformin 500 mg", etc.
#
# Rule: an entry pattern MUST start with a letter/digit (\w) so that \b is valid
# on both sides.  The validator _validate_entries() enforces this at import.
# ---------------------------------------------------------------------------

_RAW_ENTRIES: Final[tuple[tuple[str, str], ...]] = (
    # (ingredient_name, rxcui)
    ("metformin",     "6809"),
    ("empagliflozin", "1544385"),
    ("semaglutide",   "2200644"),
)

# Source version recorded at the time the RXCUIs were validated.
_SOURCE_VERSION: Final[str] = "2024-11-04"


def _validate_entries(entries: tuple[tuple[str, str], ...]) -> None:
    """Fail fast at import time if any entry would produce an unsafe regex."""
    seen_names: set[str] = set()
    seen_rxcuis: set[str] = set()
    for name, rxcui in entries:
        if not name or not re.match(r"^\w", name) or not re.search(r"\w$", name):
            raise ValueError(
                f"RxNorm entry {name!r} must start and end with a word character "
                "so that \\b word-boundary matching is safe."
            )
        if name in seen_names:
            raise ValueError(f"Duplicate ingredient name in RxNorm table: {name!r}")
        if rxcui in seen_rxcuis:
            raise ValueError(f"Duplicate RXCUI in RxNorm table: {rxcui!r}")
        seen_names.add(name)
        seen_rxcuis.add(rxcui)


_validate_entries(_RAW_ENTRIES)


@dataclass(frozen=True)
class _Entry:
    ingredient_name: str
    rxcui: str
    pattern: re.Pattern[str]  # pre-compiled, case-insensitive, word-boundary


_ENTRIES: Final[tuple[_Entry, ...]] = tuple(
    _Entry(
        ingredient_name=name,
        rxcui=rxcui,
        # \b on both sides: "metformin" matches in "metformin 500 mg"
        # and "metformin hydrochloride" but NOT in "dimetformin" (hypothetical).
        pattern=re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE),
    )
    for name, rxcui in _RAW_ENTRIES
)

# Sentinel instances shared across calls — never mutated (frozen dataclass).
_NOT_FOUND: Final[RxNormResult] = RxNormResult(
    rxcui="", ingredient_name="", status="NOT_FOUND", source_version=_SOURCE_VERSION
)
_AMBIGUOUS: Final[RxNormResult] = RxNormResult(
    rxcui="", ingredient_name="", status="AMBIGUOUS", source_version=_SOURCE_VERSION
)


def normalize_medication(medication_name: str) -> RxNormResult:
    """Normalize a free-text medication name to an ingredient-level RxNorm concept.

    Uses word-boundary-aware matching so that:
      - "metformin 500 mg"         → RESOLVED  (metformin, 6809)
      - "metformin hydrochloride"  → RESOLVED  (metformin, 6809)
      - "empagliflozin/metformin"  → AMBIGUOUS (two distinct ingredients matched)
      - "semaglutideX"             → NOT_FOUND (no word boundary at end)
      - "atorvastatin"             → NOT_FOUND (not in prototype vocabulary)

    Never raises — absence of a match is a legitimate and expected result.
    Never modifies any patient fact. Never performs dose calculations.
    Never asserts that a medication is appropriate, inappropriate, or prescribed.
    """
    text = medication_name.strip()
    if not text:
        return _NOT_FOUND

    matched = [e for e in _ENTRIES if e.pattern.search(text)]

    if len(matched) == 1:
        e = matched[0]
        return RxNormResult(
            rxcui=e.rxcui,
            ingredient_name=e.ingredient_name,
            status="RESOLVED",
            source_version=_SOURCE_VERSION,
        )
    if len(matched) > 1:
        return _AMBIGUOUS
    return _NOT_FOUND
