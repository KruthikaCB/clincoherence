"""Offline LOINC mapping coverage and isolation tests."""

import re

import pytest

from app.services.loinc_mapping import loinc_code, loinc_display

# Every (code, unit) pair accepted by LaboratoryFact.compatible_unit().
# Derived from app/schemas/clinical_fact.py — kept in sync manually.
_ALL_VALID_PAIRS = [
    ("hba1c", "%"),
    ("hba1c", "mmol/mol"),
    ("glucose", "mg/dL"),
    ("glucose", "mmol/L"),
    ("creatinine", "mg/dL"),
    ("creatinine", "umol/L"),
    ("egfr", "mL/min/1.73m2"),
    ("urine_albumin_creatinine_ratio", "mg/g"),
    ("urine_albumin_creatinine_ratio", "mg/mmol"),
]

_LOINC_PATTERN = re.compile(r"^\d{4,5}-\d$")

# All internal lab codes from LaboratoryFact.code Literal.
_ALL_LAB_CODES = frozenset(
    {"hba1c", "glucose", "creatinine", "egfr", "urine_albumin_creatinine_ratio"}
)


@pytest.mark.parametrize("code,unit", _ALL_VALID_PAIRS)
def test_every_lab_code_unit_pair_resolves(code, unit):
    result = loinc_code(code, unit)
    assert isinstance(result, str)
    assert _LOINC_PATTERN.match(result), f"LOINC code {result!r} does not match pattern"


@pytest.mark.parametrize("code,unit", _ALL_VALID_PAIRS)
def test_loinc_display_matches_known_names(code, unit):
    code_val = loinc_code(code, unit)
    display = loinc_display(code_val)
    assert isinstance(display, str) and len(display) > 0


def test_hba1c_percent_loinc():
    assert loinc_code("hba1c", "%") == "4548-4"


def test_hba1c_mmolmol_loinc():
    assert loinc_code("hba1c", "mmol/mol") == "59261-8"


def test_egfr_loinc():
    assert loinc_code("egfr", "mL/min/1.73m2") == "62238-1"


def test_all_hba1c_units_distinct_loinc():
    code_percent = loinc_code("hba1c", "%")
    code_mmol = loinc_code("hba1c", "mmol/mol")
    assert code_percent != code_mmol


def test_egfr_has_exactly_one_unit():
    # eGFR only has one accepted unit in the prototype vocabulary.
    assert loinc_code("egfr", "mL/min/1.73m2") == "62238-1"
    with pytest.raises(LookupError):
        loinc_code("egfr", "mL/min")


def test_mapping_covers_all_lab_codes():
    mapped_codes = {code for code, _ in _ALL_VALID_PAIRS}
    assert mapped_codes == _ALL_LAB_CODES, (
        "LOINC mapping does not cover all LaboratoryFact codes: "
        f"missing {_ALL_LAB_CODES - mapped_codes}"
    )


def test_unsupported_unit_raises():
    with pytest.raises(LookupError):
        loinc_code("hba1c", "mg/dL")  # hba1c does not use mg/dL


def test_unsupported_code_raises():
    with pytest.raises(LookupError):
        loinc_code("cholesterol", "%")  # not in prototype vocabulary


def test_unknown_loinc_display_raises():
    with pytest.raises(LookupError):
        loinc_display("00000-0")  # not in vocabulary


def test_loinc_code_returns_string_not_tuple():
    result = loinc_code("glucose", "mg/dL")
    assert isinstance(result, str)
    assert result == "2345-7"


def test_loinc_display_glucose_mg_dl():
    display = loinc_display("2345-7")
    assert "Glucose" in display


def test_loinc_display_hba1c_percent():
    display = loinc_display("4548-4")
    assert "Hemoglobin" in display or "A1c" in display


def test_no_patient_data_imported():
    """The module must not directly import SQLAlchemy models, DB sessions, or auth services."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "loinc_mapping.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
    forbidden = {
        "sqlalchemy",
        "app.models",
        "app.core.database",
        "app.services.authorization",
        "app.services.clinical_facts",
        "app.core.config",
        "fastapi",
    }
    for f in forbidden:
        assert not any(m == f or m.startswith(f + ".") for m in imported), (
            f"loinc_mapping directly imports forbidden module {f!r}: {imported}"
        )
