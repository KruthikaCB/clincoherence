"""Deterministic plain-text record quotation, not medical advice or interpretation."""

import json
from datetime import UTC
from decimal import Decimal

from app.schemas.consistency import FactVersion

RENDERER_VERSION = "record-restatement-text-v2"


def decimal_text(value: Decimal) -> str:
    if not value:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def render_restatement(row: FactVersion) -> str:
    """Call only on authoritative matched records. Rendering alone grants no trust.

    JSON string quoting keeps free-text fields literal; escaping HTML delimiters
    avoids treating stored descriptions as markup. Consumers must use plain text.
    """

    def quote(value: str) -> str:
        return (
            json.dumps(value, ensure_ascii=False)
            .replace("<", r"\u003c")
            .replace(">", r"\u003e")
            .replace("&", r"\u0026")
        )

    def plain(value: str) -> str:
        return quote(value)[1:-1]

    when = row.observed_at.astimezone(UTC)
    months = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    clock = f"{when.hour:02d}:{when.minute:02d}:{when.second:02d}"
    if when.microsecond:
        clock += f".{when.microsecond:06d}"
    observed = f"{when.day} {months[when.month - 1]} {when.year:04d} at {clock} UTC"
    attribution = (
        "Patient-reported" if row.attribution == "patient_reported" else "Practitioner-recorded"
    )
    fact = row.fact
    if fact.kind == "laboratory":
        labels = {
            "hba1c": "HbA1c",
            "glucose": "blood glucose",
            "creatinine": "creatinine",
            "egfr": "eGFR",
            "urine_albumin_creatinine_ratio": "urine albumin-to-creatinine ratio",
        }
        value = f"{decimal_text(fact.value)}{'' if fact.unit == '%' else ' '}{fact.unit}"
        return f"{attribution} {labels[fact.code]} was {value} on {observed}."
    if fact.kind == "medication":
        dose = (
            f"dose {decimal_text(fact.dose_value)} {plain(fact.dose_unit or '')}"
            if fact.dose_value is not None
            else "no dose recorded"
        )
        frequency = (
            f"frequency {plain(fact.frequency)}" if fact.frequency else "no frequency recorded"
        )
        return (
            f"The {attribution.lower()} record lists {plain(fact.medication_name)}, "
            f"{dose}, {frequency}, observed on {observed}. "
            "Recorded medication information does not confirm current use."
        )
    if fact.kind == "diagnosis":
        description = (
            f" Recorded description: {quote(fact.description)}." if fact.description else ""
        )
        return f"{attribution} type 2 diabetes was recorded, observed on {observed}.{description}"
    if fact.kind == "symptom":
        label = {
            "polyuria": "polyuria",
            "polydipsia": "polydipsia",
            "fatigue": "fatigue",
            "other": "a symptom",
        }[fact.code]
        return (
            f"The {attribution.lower()} record notes {label}: "
            f"{quote(fact.description)}, observed on {observed}."
        )
    label = {
        "family_history": "Family history",
        "allergy": "Allergy history",
        "smoking": "Smoking history",
        "prior_event": "Prior event",
        "other": "History",
    }[fact.category]
    return (
        f"{label} was recorded as {quote(fact.description)} on {observed} ({attribution.lower()})."
    )
