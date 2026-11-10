"""Provider contract, strict deterministic mock and local Ollama selection.

Grounding is deliberately limited to explicit timestamped statement patterns.
Unsupported natural language yields no proposals, never inferred clinical facts.
"""

import re
from typing import Protocol

from fastapi import HTTPException
from pydantic import TypeAdapter

from app.core.config import settings
from app.schemas.extraction import ExtractedCandidate
from app.services.ollama_extraction import (
    NATURAL_GROUNDING_VERSION,
    OllamaExtractionProvider,
    validate_note_candidate,
)

GROUNDING_VERSION = "explicit-statements-v1"


class ExtractionProvider(Protocol):
    name: str
    version: str

    async def extract(self, text: str) -> object: ...


NUMBER = r"[-+]?\d+(?:\.\d+)?"
LAB = re.compile(rf"(HbA1c|Glucose|Creatinine|eGFR|UACR): ({NUMBER}) (\S+) at (\S+)")
DIAGNOSIS = re.compile(r"Diagnosis: (type_2_diabetes) at (\S+)")
MEDICATION = re.compile(
    rf"Medication: ([^;\n]+?)(?:; dose: ({NUMBER}) (\S+?))?"
    r"(?:; frequency: ([^;\n]+?))? at (\S+)"
)
SYMPTOM = re.compile(r"Symptom: (polyuria|polydipsia|fatigue|other); description: (.+) at (\S+)")
HISTORY = re.compile(
    r"History: (family_history|allergy|smoking|prior_event|other); description: (.+) at (\S+)"
)


def statement(line: str) -> tuple[dict[str, object], str] | None:
    match = LAB.fullmatch(line)
    if match:
        label, value, unit, when = match.groups()
        codes = {
            "HbA1c": "hba1c",
            "Glucose": "glucose",
            "Creatinine": "creatinine",
            "eGFR": "egfr",
            "UACR": "urine_albumin_creatinine_ratio",
        }
        return {"kind": "laboratory", "code": codes[label], "value": value, "unit": unit}, when
    match = DIAGNOSIS.fullmatch(line)
    if match:
        return {"kind": "diagnosis", "code": match[1]}, match[2]
    match = MEDICATION.fullmatch(line)
    if match:
        name, dose, unit, frequency, when = match.groups()
        fact: dict[str, object] = {"kind": "medication", "medication_name": name}
        if dose is not None:
            fact.update(dose_value=dose, dose_unit=unit)
        if frequency is not None:
            fact["frequency"] = frequency
        return fact, when
    match = SYMPTOM.fullmatch(line)
    if match:
        return {"kind": "symptom", "code": match[1], "description": match[2]}, match[3]
    match = HISTORY.fullmatch(line)
    if match:
        return {"kind": "history", "category": match[1], "description": match[2]}, match[3]
    return None


def mock_output(text: str) -> list[dict[str, object]]:
    candidates = []
    offset = 0
    for segment in text.splitlines(keepends=True):
        line = segment.rstrip("\r\n")
        parsed = statement(line)
        if parsed:
            fact, when = parsed
            candidates.append(
                {
                    "fact": fact,
                    "observed_at": when,
                    "source_start": offset,
                    "source_end": offset + len(line),
                    "source_quote": line,
                }
            )
        offset += len(segment)
    return candidates


class MockExtractionProvider:
    name = "mock"
    version = "explicit-v1"

    async def extract(self, text: str) -> object:
        return mock_output(text)


def extraction_provider() -> ExtractionProvider:
    if settings.LLM_PROVIDER == "ollama":
        try:
            return OllamaExtractionProvider()
        except ValueError:
            raise HTTPException(
                503, "Configure local Ollama and qwen3:0.6b for extraction"
            ) from None
    if settings.LLM_PROVIDER != "mock":
        raise HTTPException(503, "Extraction provider unavailable")
    return MockExtractionProvider()


def validate_candidates(
    raw: object, text: str, *, grounding_version: str = GROUNDING_VERSION
) -> list[ExtractedCandidate]:
    if grounding_version not in {GROUNDING_VERSION, NATURAL_GROUNDING_VERSION}:
        raise ValueError("Unsupported grounding version")
    if not isinstance(raw, list) or len(raw) > 32:
        raise ValueError("Invalid extraction batch")
    proposals = TypeAdapter(list[ExtractedCandidate]).validate_python(raw)
    seen: set[object] = set()
    note_seen: list[ExtractedCandidate] = []
    for proposal in proposals:
        start, end = proposal.source_start, proposal.source_end
        if (
            end > len(text)
            or start >= end
            or text[start:end] != proposal.source_quote
            or (
                grounding_version == GROUNDING_VERSION
                and (
                    (start > 0 and text[start - 1] not in "\r\n")
                    or (end < len(text) and text[end] not in "\r\n")
                )
            )
        ):
            raise ValueError("Unsupported source span")
        if grounding_version == NATURAL_GROUNDING_VERSION:
            validate_note_candidate(proposal)
            if any(
                previous.source_start == start
                and previous.source_end == end
                and previous.fact == proposal.fact
                for previous in note_seen
            ):
                raise ValueError("Duplicate extraction proposal")
            note_seen.append(proposal)
            continue
        parsed = statement(proposal.source_quote)
        if parsed is None:
            raise ValueError("Unsupported source statement")
        fact, when = parsed
        expected = ExtractedCandidate.model_validate(
            {
                "fact": fact,
                "observed_at": when,
                "source_start": start,
                "source_end": end,
                "source_quote": proposal.source_quote,
            }
        )
        if proposal.fact != expected.fact or proposal.observed_at != expected.observed_at:
            raise ValueError("Proposal does not match the source")
        if (start, end) in seen:
            raise ValueError("Duplicate extraction proposal")
        seen.add((start, end))
    return proposals
