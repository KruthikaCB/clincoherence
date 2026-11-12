"""Optional capstone segment: one real Ollama inference, no database or app API.

Run with the existing backend venv Python using -I -B; no arguments accepted.
Only this process receives Ollama settings. The running mock app is untouched.
"""

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
BACKEND = Path(__file__).resolve().parents[1]
REPORT = BACKEND.parent / "docs/demo-review-2026-11-17/OLLAMA_VERIFICATION_REPORT.md"
NOTE = "Patient has type 2 diabetes at 2026-02-05T09:00:00Z."
DEADLINE_SECONDS = 30
TEST_ENV = {
    "APP_ENV": "production",
    "LLM_PROVIDER": "ollama",
    "LLM_BASE_URL": "http://127.0.0.1:11434",
    "LLM_MODEL_NAME": "qwen3:0.6b",
    "LLM_EXTRACTION_TIMEOUT_SECONDS": str(DEADLINE_SECONDS),
    "ENABLE_CLINICIAN_FACT_REVIEW": "false",
    "ENABLE_DEMO_PATIENTS": "false",
    "ENABLE_DEMO_REGISTRATION": "false",
    "DATABASE_URL": "postgresql+asyncpg://blocked:blocked@127.0.0.1:1/not_used",
}


def deny_database(*args: object, **kwargs: object) -> None:
    raise RuntimeError("PostgreSQL access is prohibited in this demonstration")


def historical_safety_example() -> None:
    print("\nHISTORICAL SAFETY EXAMPLE -- observed 12 November 2026; NOT a live replay")
    print("Input stated only that the synthetic patient takes metformin, with a timestamp.")
    print('The model invented "500 mg twice daily". Those fields were absent from the note.')
    print('JSON schema passed, but grounding rejected it: "Medication dose not grounded".')
    print("The adapter withheld the result (OllamaOutputError). No dose is advice or an order.")
    print("We do not request or depend on reproducing that hallucination during this segment.")


async def demonstrate() -> None:
    # Imported only after environment/dotenv/DB guards are active.
    from app.services.extraction_provider import validate_candidates
    from app.services.ollama_extraction import (
        NATURAL_GROUNDING_VERSION,
        OllamaExtractionProvider,
    )

    provider = OllamaExtractionProvider()
    if provider.model != "qwen3:0.6b" or provider.url != "http://127.0.0.1:11434/api/chat":
        raise RuntimeError("Unexpected inference target")
    print(f"Adapter: {provider.name} / {provider.version}; CPU; thinking disabled")
    print(f"One request; {DEADLINE_SECONDS}-second deadline; no automatic retry.", flush=True)
    started = time.perf_counter()
    try:
        async with asyncio.timeout(DEADLINE_SECONDS):
            raw = await provider.extract(NOTE)
        candidates = validate_candidates(raw, NOTE, grounding_version=NATURAL_GROUNDING_VERSION)
        if len(candidates) != 1:
            raise ValueError("Expected exactly one supported diagnosis candidate")
        candidate = candidates[0]
        if (
            candidate.fact.kind != "diagnosis"
            or candidate.fact.code != "type_2_diabetes"
            or candidate.observed_at != datetime(2026, 2, 5, 9, tzinfo=UTC)
            or candidate.source_quote != NOTE
            or candidate.source_start != 0
            or candidate.source_end != len(NOTE)
        ):
            raise ValueError("Candidate does not match the fixed synthetic scenario")
        print("\nACTUAL OLLAMA CANDIDATE (unaccepted proposal):")
        print(json.dumps(candidate.model_dump(mode="json"), indent=2, ensure_ascii=True))
        print("\nSupporting source quote:")
        print(candidate.source_quote)
        print("Validation: PASS -- schema, source grounding, timestamp and expected diagnosis.")
        print("No patient record was created or accepted. Human review is still required.")
    finally:
        print(f"Inference + validation execution time: {time.perf_counter() - started:.3f} seconds")


def main() -> int:
    print("CLINCOHERENCE -- REAL LOCAL OLLAMA INFERENCE, NOT MOCK EXTRACTION")
    print("Synthetic, provider-only demonstration; no PostgreSQL or frontend calls.")
    print("Input note:\n" + NOTE + "\n", flush=True)
    status = 0
    try:
        if len(sys.argv) != 1:
            raise ValueError("This fixed synthetic demonstration takes no arguments")
        import asyncpg
        from pydantic_settings.sources import DotEnvSettingsSource

        # -I excludes cwd/PYTHONPATH; add only the script's resolved backend root.
        sys.path.insert(0, str(BACKEND))
        with (
            patch.dict(os.environ, TEST_ENV, clear=True),
            patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}),
            patch.object(asyncpg, "connect", deny_database),
            patch.object(asyncpg, "create_pool", deny_database),
        ):
            asyncio.run(demonstrate())
    except KeyboardInterrupt:
        print("\nValidation: NOT COMPLETED -- presenter cancelled the live request.")
        status = 130
    except Exception as error:
        # Avoid printing driver/configuration details; never substitute mock output.
        print(f"\nValidation: FAIL / UNAVAILABLE ({type(error).__name__}).")
        print("No validated candidate released. No automatic retry or mock substitution.")
        status = 1
    historical_safety_example()
    print("\nFallback reference (previously recorded evidence, NOT current inference):")
    print(REPORT)
    print("If the live request fails, open that report and return to the unchanged mock demo.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
