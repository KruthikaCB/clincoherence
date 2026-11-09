"""Untrusted proposals, speaker-only decisions and atomic accepted-fact creation."""

import asyncio
import hashlib
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ClinicalFactVersion,
    ConfirmationDecision,
    ConfirmationProposal,
    ExtractionMessage,
)
from app.schemas.clinical_fact import FactProvenance
from app.schemas.extraction import (
    ConfirmationResponse,
    ExtractedCandidate,
    ExtractionResponse,
    ExtractMessage,
    ProviderMetadata,
)
from app.services.authorization import authorize_patient_scope
from app.services.clinical_facts import lock_patient_scope
from app.services.consents import clock
from app.services.extraction_provider import (
    GROUNDING_VERSION,
    ExtractionProvider,
    validate_candidates,
)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def candidate(row: ConfirmationProposal) -> ExtractedCandidate:
    return ExtractedCandidate.model_validate(
        {
            "fact": row.payload,
            "observed_at": row.observed_at,
            "source_start": row.source_start,
            "source_end": row.source_end,
            "source_quote": row.source_quote,
        }
    )


def output(
    row: ConfirmationProposal,
    source: ExtractionMessage,
    decision: ConfirmationDecision | None = None,
) -> ConfirmationResponse:
    try:
        proposal = candidate(row)
        return ConfirmationResponse.model_validate(
            {
                "confirmation_id": row.id,
                "patient_id": row.patient_id,
                "source_message_id": source.id,
                "speaker_id": row.speaker_id,
                "speaker_role": row.speaker_role,
                "attribution": "patient_reported"
                if row.speaker_role == "patient"
                else "practitioner_managed",
                "proposed_fact": proposal.fact,
                "observed_at": row.observed_at,
                "source_start": row.source_start,
                "source_end": row.source_end,
                "source_quote": row.source_quote,
                "source_sha256": source.source_sha256,
                "data_origin": source.data_origin,
                "provider": {"name": source.provider_name, "version": source.provider_version},
                "grounding_version": source.grounding_version,
                "created_at": row.created_at,
                "status": decision.status if decision else "PENDING",
                "decided_at": decision.decided_at if decision else None,
                "accepted_version_id": decision.accepted_version_id if decision else None,
            }
        )
    except ValidationError:
        raise HTTPException(409, "Stored confirmation failed validation") from None


async def extract_message(
    db: AsyncSession,
    account_id: UUID,
    patient_id: UUID,
    payload: ExtractMessage,
    provider: ExtractionProvider,
) -> ExtractionResponse:
    await authorize_patient_scope(db, account_id, patient_id)
    exists = await db.scalar(
        select(ExtractionMessage.id).where(ExtractionMessage.id == payload.source_message_id)
    )
    if exists is not None:
        raise HTTPException(409, "Source message already submitted")
    # Close the read transaction before provider work. No patient locks or DB
    # snapshot survive a future slow provider call; all authority is rechecked.
    await db.commit()
    try:
        metadata = ProviderMetadata(name=provider.name, version=provider.version)
        async with asyncio.timeout(10):
            raw = await provider.extract(payload.text)
        proposals = validate_candidates(raw, payload.text)
    except Exception:
        # Provider output, source text and exception details must not reach logs.
        raise HTTPException(502, "Extraction output unavailable or unsupported") from None
    account = await lock_patient_scope(db, account_id, patient_id)
    source = ExtractionMessage(
        id=payload.source_message_id,
        patient_id=patient_id,
        speaker_id=account_id,
        speaker_role=account.role,
        text=payload.text,
        data_origin=payload.data_origin,
        source_sha256=sha256(payload.text),
        provider_name=metadata.name,
        provider_version=metadata.version,
        grounding_version=GROUNDING_VERSION,
    )
    db.add(source)
    rows = []
    try:
        await db.flush()
        for ordinal, proposal in enumerate(proposals):
            row = ConfirmationProposal(
                message_id=source.id,
                patient_id=patient_id,
                speaker_id=account_id,
                speaker_role=account.role,
                ordinal=ordinal,
                payload=proposal.fact.model_dump(mode="json"),
                observed_at=proposal.observed_at,
                source_start=proposal.source_start,
                source_end=proposal.source_end,
                source_quote=proposal.source_quote,
            )
            db.add(row)
            rows.append(row)
        await db.flush()
        result = ExtractionResponse(
            source_message_id=source.id, confirmations=[output(row, source) for row in rows]
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            409, "Source message already submitted or extraction conflict"
        ) from None
    except BaseException:
        await db.rollback()
        raise
    return result


async def own_confirmations(
    db: AsyncSession, account_id: UUID, patient_id: UUID
) -> list[ConfirmationResponse]:
    account = await lock_patient_scope(db, account_id, patient_id)
    rows = await db.execute(
        select(ConfirmationProposal, ExtractionMessage, ConfirmationDecision)
        .join(ExtractionMessage, ExtractionMessage.id == ConfirmationProposal.message_id)
        .outerjoin(
            ConfirmationDecision, ConfirmationDecision.proposal_id == ConfirmationProposal.id
        )
        .where(
            ConfirmationProposal.patient_id == patient_id,
            ConfirmationProposal.speaker_id == account_id,
            ConfirmationProposal.speaker_role == account.role,
        )
        .order_by(
            ConfirmationProposal.created_at,
            ConfirmationProposal.message_id,
            ConfirmationProposal.ordinal,
        )
    )
    return [output(proposal, source, decision) for proposal, source, decision in rows]


async def decide(
    db: AsyncSession, account_id: UUID, patient_id: UUID, confirmation_id: UUID, *, confirm: bool
) -> ConfirmationResponse:
    account = await lock_patient_scope(db, account_id, patient_id)
    proposal = await db.scalar(
        select(ConfirmationProposal)
        .where(
            ConfirmationProposal.id == confirmation_id,
            ConfirmationProposal.patient_id == patient_id,
            ConfirmationProposal.speaker_id == account_id,
            ConfirmationProposal.speaker_role == account.role,
        )
        .with_for_update()
    )
    if proposal is None:
        raise HTTPException(404, "Confirmation not found")
    if await db.get(ConfirmationDecision, confirmation_id) is not None:
        raise HTTPException(409, "Confirmation already decided")
    source = await db.get(ExtractionMessage, proposal.message_id)
    if source is None:
        raise HTTPException(409, "Confirmation source unavailable")
    try:
        proposed = candidate(proposal)
        if (
            source.source_sha256 != sha256(source.text)
            or source.grounding_version != GROUNDING_VERSION
        ):
            raise ValueError("Invalid extraction provenance")
        validate_candidates([proposed.model_dump(mode="json")], source.text)
    except (ValidationError, ValueError):
        raise HTTPException(409, "Stored proposal failed grounding or validation") from None
    now = await clock(db)
    accepted_id = None
    if confirm:
        accepted_id = uuid4()
        provenance = FactProvenance(
            source="individually confirmed extraction",
            reference=(
                f"message:{source.id}/confirmation:{proposal.id}/"
                f"provider:{source.provider_name}@{source.provider_version}/"
                f"grounding:{source.grounding_version}/sha256:{source.source_sha256}/"
                f"span:{proposal.source_start}-{proposal.source_end}"
            ),
            data_origin=source.data_origin,
        )
        db.add(
            ClinicalFactVersion(
                id=accepted_id,
                fact_id=accepted_id,
                patient_id=patient_id,
                version=1,
                author_id=account_id,
                author_role=account.role,
                attribution="patient_reported"
                if account.role == "patient"
                else "practitioner_managed",
                payload=proposed.fact.model_dump(mode="json"),
                observed_at=proposed.observed_at,
                provenance=provenance.model_dump(mode="json"),
                entry_method="LLM_EXTRACTED",
                status="CONFIRMED",
                confirmed_by_id=account_id,
                confirmed_at=now,
            )
        )
    decision = ConfirmationDecision(
        proposal_id=proposal.id,
        patient_id=patient_id,
        speaker_id=account_id,
        speaker_role=account.role,
        decided_by_id=account_id,
        status="CONFIRMED" if confirm else "REJECTED",
        accepted_version_id=accepted_id,
        accepted_entry_method="LLM_EXTRACTED" if confirm else None,
        decided_at=now,
    )
    try:
        # Flush the accepted fact before its decision FK, but never commit it alone.
        await db.flush()
        db.add(decision)
        await db.flush()
        await authorize_patient_scope(db, account_id, patient_id)
        result = output(proposal, source, decision)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Confirmation already decided or decision conflict") from None
    except BaseException:
        await db.rollback()
        raise
    return result
