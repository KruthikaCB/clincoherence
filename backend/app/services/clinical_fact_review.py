"""Clinician-specific review history under the existing consent/correction gate.

These events are not clinical recommendations or reusable authorization permits.
"""

from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClinicalFactReviewEvent as Event
from app.models import ClinicalFactVersion, User
from app.schemas.clinical_fact_review import (
    POLICY_VERSION,
    ClinicalFactReview,
    CreateClinicalFactReview,
    CurrentClinicalFactReview,
)
from app.services.authorization import authorize_patient_scope
from app.services.clinical_facts import current_query, lock_patient_scope, output


async def reviewer_scope(db: AsyncSession, account_id: UUID, patient_id: UUID) -> User:
    account = await lock_patient_scope(db, account_id, patient_id)
    if account.role != "practitioner":
        raise HTTPException(403, "Clinician fact review requires a practitioner")
    return account


def event_output(event: Event) -> ClinicalFactReview:
    return ClinicalFactReview.model_validate(
        {name: getattr(event, name) for name in ClinicalFactReview.model_fields}
    )


def validate_transition(previous: Event | None, payload: CreateClinicalFactReview) -> None:
    if payload.expected_review_id != (previous.id if previous else None):
        raise HTTPException(409, "Review changed; reload before deciding")
    if payload.decision == "WITHDRAW" and (previous is None or previous.decision != "APPROVE"):
        raise HTTPException(409, "Only an active approval can be withdrawn")


async def create_review(
    db: AsyncSession,
    account_id: UUID,
    patient_id: UUID,
    fact_id: UUID,
    payload: CreateClinicalFactReview,
) -> ClinicalFactReview:
    await reviewer_scope(db, account_id, patient_id)
    # Reauthorize before retry lookup. A retry returns a historical event, not
    # a claim that its approval is still current, and never reactivates it.
    existing = await db.scalar(
        select(Event).where(
            Event.patient_id == patient_id,
            Event.reviewer_id == account_id,
            Event.operation_id == payload.operation_id,
        )
    )
    if existing is not None:
        if (
            existing.fact_id != fact_id
            or existing.fact_version_id != payload.expected_version_id
            or existing.previous_review_id != payload.expected_review_id
            or existing.decision != payload.decision
            or existing.reason != payload.reason
            or existing.policy_version != POLICY_VERSION
        ):
            raise HTTPException(409, "Operation already used for a different review")
        return event_output(existing)

    target = await db.scalar(
        select(ClinicalFactVersion).where(
            ClinicalFactVersion.patient_id == patient_id,
            ClinicalFactVersion.fact_id == fact_id,
            ClinicalFactVersion.id == payload.expected_version_id,
            ClinicalFactVersion.status == "CONFIRMED",
        )
    )
    if target is None:
        raise HTTPException(404, "Accepted fact version not found")
    output(target)  # Validate persisted accepted fact without exposing its payload.
    current = await db.scalar(
        current_query(patient_id).where(ClinicalFactVersion.fact_id == fact_id)
    )
    # A clinician may withdraw their approval of an old version after correction.
    if payload.decision != "WITHDRAW" and (current is None or current.id != target.id):
        raise HTTPException(409, "Fact version changed; reload before deciding")
    previous = await db.scalar(
        select(Event)
        .where(
            Event.patient_id == patient_id,
            Event.fact_version_id == target.id,
            Event.reviewer_id == account_id,
        )
        .order_by(Event.sequence.desc())
        .limit(1)
    )
    validate_transition(previous, payload)
    event = Event(
        id=uuid4(),
        patient_id=patient_id,
        fact_id=fact_id,
        fact_version_id=target.id,
        fact_status="CONFIRMED",
        reviewer_id=account_id,
        operation_id=payload.operation_id,
        sequence=previous.sequence + 1 if previous else 1,
        previous_review_id=previous.id if previous else None,
        previous_sequence=previous.sequence if previous else None,
        previous_decision=previous.decision if previous else None,
        decision=payload.decision,
        reason=payload.reason,
        policy_version=POLICY_VERSION,
    )
    try:
        db.add(event)
        await authorize_patient_scope(db, account_id, patient_id)
        await db.flush()
        result = event_output(event)
        await db.commit()
        return result
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Review write conflict; reload before retrying") from None
    except DBAPIError:
        await db.rollback()
        # A failed acknowledgement may follow a committed transaction. Reuse the
        # operation ID so a retry retrieves that receipt instead of duplicating it.
        raise HTTPException(
            503, "Review storage unavailable; retry with the same operation ID"
        ) from None
    except BaseException:
        await db.rollback()
        raise


async def current_reviews(
    db: AsyncSession,
    account_id: UUID,
    patient_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[CurrentClinicalFactReview]:
    await reviewer_scope(db, account_id, patient_id)
    latest_id = (
        select(Event.id)
        .where(
            Event.patient_id == patient_id,
            Event.reviewer_id == account_id,
            Event.fact_version_id == ClinicalFactVersion.id,
        )
        .order_by(Event.sequence.desc())
        .limit(1)
        .correlate(ClinicalFactVersion)
        .scalar_subquery()
    )
    rows = await db.execute(
        current_query(patient_id)
        .add_columns(Event)
        .outerjoin(Event, Event.id == latest_id)
        .order_by(ClinicalFactVersion.fact_id)
        .limit(limit)
        .offset(offset)
    )
    result = []
    for fact, event in rows:
        output(fact)
        status = (
            {"APPROVE": "APPROVED", "REJECT": "REJECTED", "WITHDRAW": "WITHDRAWN"}[event.decision]
            if event else "UNREVIEWED"
        )
        result.append(
            CurrentClinicalFactReview.model_validate(dict(
                patient_id=patient_id,
                fact_id=fact.fact_id,
                fact_version_id=fact.id,
                reviewer_id=account_id,
                review_status=status,
                latest_review=event_output(event) if event else None,
            ))
        )
    return result


async def review_history(
    db: AsyncSession,
    account_id: UUID,
    patient_id: UUID,
    fact_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[ClinicalFactReview]:
    await reviewer_scope(db, account_id, patient_id)
    target = await db.scalar(
        current_query(patient_id).where(ClinicalFactVersion.fact_id == fact_id)
    )
    if target is None:
        raise HTTPException(404, "Accepted fact not found")
    rows = await db.scalars(
        select(Event)
        .where(
            Event.patient_id == patient_id,
            Event.fact_id == fact_id,
            Event.reviewer_id == account_id,
        )
        .order_by(Event.recorded_at, Event.id)
        .limit(limit)
        .offset(offset)
    )
    return [event_output(row) for row in rows]
