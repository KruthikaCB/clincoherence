"""Static LOINC terminology mapping for the ClinCoherence T2DM prototype vocabulary.

Maps (laboratory code, unit) pairs from confirmed patient LaboratoryFact records
to their corresponding LOINC codes. This is a read-only terminology lookup; it
never modifies patient records, performs unit conversion, or makes clinical judgments.

LOINC® is copyright © 1995–2024 Regenstrief Institute, Inc.
Used under the LOINC license (https://loinc.org/license/).
Codes validated against LOINC release 2.78 (2024-08).
"""

from typing import Final

# (internal_code, unit) -> (loinc_code, loinc_long_common_name)
# Every (code, unit) pair accepted by LaboratoryFact.compatible_unit() is covered.
# Mapping is 1:1: each code+unit combination resolves to exactly one LOINC code.
_TABLE: Final[dict[tuple[str, str], tuple[str, str]]] = {
    ("hba1c", "%"): (
        "4548-4",
        "Hemoglobin A1c/Hemoglobin.total in Blood",
    ),
    ("hba1c", "mmol/mol"): (
        "59261-8",
        "Hemoglobin A1c/Hemoglobin.total in Blood by IFCC protocol",
    ),
    ("glucose", "mg/dL"): (
        "2345-7",
        "Glucose [Mass/volume] in Serum or Plasma",
    ),
    ("glucose", "mmol/L"): (
        "15074-8",
        "Glucose [Moles/volume] in Blood",
    ),
    ("creatinine", "mg/dL"): (
        "2160-0",
        "Creatinine [Mass/volume] in Serum or Plasma",
    ),
    ("creatinine", "umol/L"): (
        "14682-9",
        "Creatinine [Moles/volume] in Serum or Plasma",
    ),
    ("egfr", "mL/min/1.73m2"): (
        "62238-1",
        "Glomerular filtration rate/1.73 sq M.predicted [Volume Rate/Area] "
        "in Serum, Plasma or Blood by Creatinine-based formula (CKD-EPI 2021)",
    ),
    ("urine_albumin_creatinine_ratio", "mg/g"): (
        "9318-7",
        "Albumin/Creatinine [Mass ratio] in Urine",
    ),
    ("urine_albumin_creatinine_ratio", "mg/mmol"): (
        "77140-2",
        "Albumin/Creatinine [Moles ratio] in Urine",
    ),
}

# Reverse lookup: loinc_code -> loinc_long_common_name
_DISPLAY: Final[dict[str, str]] = {code: name for code, name in _TABLE.values()}


def loinc_code(code: str, unit: str) -> str:
    """Return the LOINC code for a confirmed laboratory fact's code+unit pair.

    Raises LookupError if the combination is not in the prototype vocabulary.
    Every (code, unit) pair accepted by LaboratoryFact.compatible_unit() is mapped;
    a LookupError therefore indicates an unsupported extension, not a patient error.

    This is a static terminology mapping, not a clinical interpretation.
    No unit conversion is performed. No patient record is read or modified.
    """
    key = (code, unit)
    if key not in _TABLE:
        raise LookupError(
            f"LOINC mapping unavailable for laboratory code {code!r} with unit {unit!r}. "
            "Only prototype T2DM vocabulary is supported."
        )
    return _TABLE[key][0]


def loinc_display(loinc: str) -> str:
    """Return the LOINC long common name for a mapped code.

    Raises LookupError if the code is not in the prototype vocabulary.
    """
    if loinc not in _DISPLAY:
        raise LookupError(
            f"LOINC display name unavailable for code {loinc!r}. "
            "Only codes in the prototype T2DM vocabulary are supported."
        )
    return _DISPLAY[loinc]
