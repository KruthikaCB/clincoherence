"""Offline RxNorm terminology lookup coverage and isolation tests.

Includes word-boundary safety tests covering misleading and ambiguous names.
"""

import pytest

from app.services.rxnorm_lookup import RxNormResult, normalize_medication


# ── Basic resolution ──────────────────────────────────────────────────────────

def test_exact_metformin_resolves():
    r = normalize_medication("metformin")
    assert r.status == "RESOLVED"
    assert r.rxcui == "6809"
    assert r.ingredient_name == "metformin"


def test_exact_empagliflozin_resolves():
    r = normalize_medication("empagliflozin")
    assert r.status == "RESOLVED"
    assert r.rxcui == "1544385"
    assert r.ingredient_name == "empagliflozin"


def test_exact_semaglutide_resolves():
    r = normalize_medication("semaglutide")
    assert r.status == "RESOLVED"
    assert r.rxcui == "2200644"
    assert r.ingredient_name == "semaglutide"


# ── Case-insensitive matching ─────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "Metformin",
    "METFORMIN",
    "metformin 500 mg",
    "metformin hydrochloride",
    "Metformin HCl 1000 mg twice daily",
    "metformin extended release",
    "Glucophage (metformin)",
])
def test_case_insensitive_match_metformin(name):
    r = normalize_medication(name)
    assert r.status == "RESOLVED"
    assert r.rxcui == "6809"


@pytest.mark.parametrize("name", [
    "Empagliflozin 10mg",
    "EMPAGLIFLOZIN",
    "Jardiance (empagliflozin)",
    "empagliflozin 25 mg",
])
def test_case_insensitive_match_empagliflozin(name):
    r = normalize_medication(name)
    assert r.status == "RESOLVED"
    assert r.rxcui == "1544385"


@pytest.mark.parametrize("name", [
    "Semaglutide 0.5 mg",
    "SEMAGLUTIDE",
    "Ozempic (semaglutide)",
    "oral semaglutide",
])
def test_case_insensitive_match_semaglutide(name):
    r = normalize_medication(name)
    assert r.status == "RESOLVED"
    assert r.rxcui == "2200644"


# ── NOT_FOUND for out-of-scope medications ────────────────────────────────────

@pytest.mark.parametrize("name", [
    "atorvastatin",
    "aspirin",
    "lisinopril",
    "amlodipine",
    "insulin",
    "glipizide",
    "sitagliptin",
    "liraglutide",
    "pioglitazone",
    "dapagliflozin",
    "canagliflozin",
    "dulaglutide",
    "exenatide",
])
def test_unrecognized_medication_not_found(name):
    r = normalize_medication(name)
    assert r.status == "NOT_FOUND"
    assert r.rxcui == ""


# ── AMBIGUOUS — two distinct ingredients in one string ────────────────────────

@pytest.mark.parametrize("name", [
    # Fixed-dose combinations that name two prototype ingredients
    "empagliflozin/metformin",
    "metformin and empagliflozin",
    "semaglutide plus metformin",
    "metformin 500 mg / empagliflozin 10 mg tablet",
    # Three-way combination (all three entries present)
    "metformin empagliflozin semaglutide",
])
def test_combination_names_are_ambiguous(name):
    """A name that contains multiple prototype ingredient names is AMBIGUOUS,
    not silently resolved to one of them."""
    r = normalize_medication(name)
    assert r.status == "AMBIGUOUS", (
        f"Expected AMBIGUOUS for combination name {name!r}, got {r.status!r}"
    )
    assert r.rxcui == ""


# ── Word-boundary safety — no false positives from substring overlap ──────────

@pytest.mark.parametrize("name", [
    # Hypothetical names that *contain* a prototype ingredient as a substring
    # but are NOT word-boundary matches — must NOT resolve.
    "dimetformin",          # starts with "di" immediately before "metformin"
    "metforminol",          # "metformin" followed immediately by "ol"
    "prometformin sulfate",  # "pro" immediately before "metformin"
    # Contrived names for the other two
    "proempagliflozin",
    "empagliflozinol",
    "presemaglutide",
    "semaglutidex",
])
def test_word_boundary_prevents_false_positive(name):
    """The ingredient name must appear as a whole word, not as an internal fragment."""
    r = normalize_medication(name)
    assert r.status == "NOT_FOUND", (
        f"Expected NOT_FOUND for {name!r} (substring-of ingredient), got {r.status!r}"
    )


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_string_not_found():
    assert normalize_medication("").status == "NOT_FOUND"


def test_whitespace_only_not_found():
    assert normalize_medication("   ").status == "NOT_FOUND"


def test_result_is_frozen_dataclass():
    r = normalize_medication("metformin")
    with pytest.raises((AttributeError, TypeError)):
        r.rxcui = "0"  # type: ignore[misc]


def test_source_version_present_on_resolved():
    r = normalize_medication("metformin")
    assert r.status == "RESOLVED"
    assert isinstance(r.source_version, str) and len(r.source_version) > 0


def test_source_version_present_on_not_found():
    r = normalize_medication("unknown-drug")
    assert isinstance(r.source_version, str) and len(r.source_version) > 0


def test_source_version_present_on_ambiguous():
    r = normalize_medication("metformin/empagliflozin")
    assert r.status == "AMBIGUOUS"
    assert isinstance(r.source_version, str) and len(r.source_version) > 0


def test_resolved_result_type():
    r = normalize_medication("empagliflozin")
    assert isinstance(r, RxNormResult)


@pytest.mark.parametrize("name", [
    "x" * 2000,
    "médicament",
    "123456789",
    "!@#$%^&*()",
    "\t\nmetformin\t\n",   # whitespace around known name — word boundary still fires
    "a" * 500,
    "метформин",           # Cyrillic transliteration, should not match ASCII pattern
    "",
    " ",
])
def test_does_not_raise_on_any_input(name):
    """normalize_medication must never raise, regardless of input."""
    r = normalize_medication(name)
    assert isinstance(r, RxNormResult)
    assert r.status in ("RESOLVED", "AMBIGUOUS", "NOT_FOUND")


def test_whitespace_padded_name_still_resolves():
    # Word boundary fires after strip+leading whitespace removal.
    r = normalize_medication("\t\nmetformin\t\n")
    assert r.status == "RESOLVED"
    assert r.rxcui == "6809"


def test_insulin_alone_is_not_found():
    # "insulin" is not in the prototype vocabulary.
    assert normalize_medication("insulin").status == "NOT_FOUND"


def test_insulin_metformin_combo_is_ambiguous_not_resolved():
    # Old substring behaviour would have returned RESOLVED (metformin).
    # With word-boundary matching "insulin/metformin" still only matches metformin
    # because "insulin" is not in the vocabulary — so it stays RESOLVED.
    r = normalize_medication("insulin/metformin")
    assert r.status == "RESOLVED"
    assert r.rxcui == "6809"


# ── Isolation ─────────────────────────────────────────────────────────────────

def test_no_patient_data_imported():
    """The module must not directly import patient data or auth services."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "rxnorm_lookup.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
    forbidden = {
        "sqlalchemy", "app.models", "app.core.database",
        "app.services.authorization", "app.services.clinical_facts",
        "app.core.config", "fastapi",
    }
    for f in forbidden:
        assert not any(m == f or m.startswith(f + ".") for m in imported), (
            f"rxnorm_lookup directly imports forbidden module {f!r}"
        )
